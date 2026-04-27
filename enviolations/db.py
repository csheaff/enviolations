from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from .config import DB_PATH

# Mirrors Facility._COUNTY_SUFFIX_RE in models.py — strips county-type suffixes
# and uppercases so that unified_facilities county is consistent across sources.
_COUNTY_SUFFIX_RE = re.compile(
    r"\s+(?:county|parish|borough|census\s+area|municipality|city\s+and\s+borough)$",
    re.IGNORECASE,
)


def _normalize_county_storage(val: str | None) -> str | None:
    """Normalize a county name for storage: strip suffix, uppercase.

    Matches the Facility._normalize_county Pydantic validator so that
    county values in unified_facilities are consistent regardless of whether
    they came through the model validator or from raw DB data.
    """
    if not val:
        return val
    v = _COUNTY_SUFFIX_RE.sub("", val.strip()).strip()
    return v.upper() if v else None

logger = logging.getLogger(__name__)

# EPA sources use globally unique RegistryIDs as source_id (no state-level overlap).
# Non-EPA state sources share overlapping source_id namespaces across states
# (e.g. 'brownfield-108' appears in ID, KY, NJ, NM DEP/DEQ sources).
_EPA_SOURCES_SET = frozenset(
    ("epa_echo", "epa_rcra", "epa_caa", "epa_sdwa", "epa_sems")
)
# SQL CASE expression for canonical_id: EPA keeps bare source_id; non-EPA is qualified.
_CANONICAL_ID_EXPR = (
    "CASE WHEN source IN ('epa_echo','epa_rcra','epa_caa','epa_sdwa','epa_sems') "
    "THEN source_id ELSE source || '/' || source_id END"
)


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=120, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-64000")    # 64MB (default ~2MB)
    conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def get_batch_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Connection optimized for batch operations like scoring."""
    conn = get_connection(db_path)
    conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL
    conn.execute("PRAGMA cache_size=-128000")   # 128MB
    conn.execute("PRAGMA mmap_size=2147483648") # 2GB mmap
    return conn


def _ensure_ingestion_log_view(conn: sqlite3.Connection) -> None:
    """Ensure the backward-compatible ingestion_log VIEW exists.

    After the 002 migration renames ingestion_log -> pipeline_ops, API code
    that references ingestion_log needs this VIEW. This guard handles the case
    where a previous migration attempt created pipeline_ops but not the VIEW
    (e.g. from a partial/interrupted migration).
    """
    has_view = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name='ingestion_log'"
    ).fetchone()
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_ops'"
    ).fetchone()
    if has_table and not has_view:
        conn.execute(
            "CREATE VIEW IF NOT EXISTS ingestion_log AS "
            "SELECT id, source, started_at, completed_at, status, "
            "records_processed, error_message "
            "FROM pipeline_ops"
        )
        logger.info("Created backward-compatible ingestion_log VIEW")


def _ensure_facility_matches(conn: sqlite3.Connection) -> None:
    """Seed facility_matches with self-matches if empty but facilities exist.

    Handles the migration case: new code deployed on a DB that has facilities
    but no facility_matches rows yet. Without this, the unified_facilities
    VIEW (which JOINs through facility_matches) returns 0 rows.
    """
    has_facilities = conn.execute("SELECT 1 FROM facilities LIMIT 1").fetchone()
    if not has_facilities:
        return  # Empty DB, nothing to seed

    has_matches = conn.execute("SELECT 1 FROM facility_matches LIMIT 1").fetchone()
    if has_matches:
        return  # Already populated

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO facility_matches "
        "(source, source_id, canonical_id, match_tier, match_score, matched_at) "
        f"SELECT source, source_id, {_CANONICAL_ID_EXPR}, 0, 1.0, ? FROM facilities",
        (now,),
    )
    conn.commit()


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    run_alembic_migrations(path)
    conn = get_connection(db_path)
    # Verify critical tables exist (catches interrupted migration)
    for table in ("facilities", "violations", "facility_scores"):
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone():
            conn.close()
            raise RuntimeError(
                f"Table '{table}' missing after migration. "
                "The database may be corrupted from an interrupted migration. "
                f"Delete {path} and restart, or restore from backup."
            )
    _ensure_facility_matches(conn)
    _ensure_ingestion_log_view(conn)
    cleanup_stale_ingestions(conn)
    cleanup_stale_api_usage(conn)
    conn.commit()
    return conn


_UNIFIED_QUERY = """
SELECT
    fm.canonical_id AS source_id,
    COALESCE(
        MAX(CASE WHEN f.source = 'epa_echo'   AND f.name != 'Unknown' THEN f.name END),
        MAX(CASE WHEN f.source = 'epa_rcra'   AND f.name != 'Unknown' THEN f.name END),
        MAX(CASE WHEN f.source = 'epa_caa'    AND f.name != 'Unknown' THEN f.name END),
        MAX(CASE WHEN f.source = 'epa_sdwa'   AND f.name != 'Unknown' THEN f.name END),
        MAX(CASE WHEN f.source = 'epa_sems'   AND f.name != 'Unknown' THEN f.name END),
        MAX(CASE WHEN f.source || '/' || f.source_id = fm.canonical_id AND f.name != 'Unknown' THEN f.name END),
        MAX(CASE WHEN f.name != 'Unknown' THEN f.name END),
        MAX(f.name)
    ) AS name,
    COALESCE(
        MAX(CASE WHEN f.source || '/' || f.source_id = fm.canonical_id AND f.address IS NOT NULL AND f.address != '' THEN f.address END),
        MAX(CASE WHEN f.source = 'epa_echo'  AND f.address IS NOT NULL AND f.address != '' THEN f.address END),
        MAX(CASE WHEN f.source = 'epa_rcra'  AND f.address IS NOT NULL AND f.address != '' THEN f.address END),
        MAX(CASE WHEN f.source = 'epa_caa'   AND f.address IS NOT NULL AND f.address != '' THEN f.address END),
        MAX(CASE WHEN f.source = 'epa_sdwa'  AND f.address IS NOT NULL AND f.address != '' THEN f.address END),
        MAX(CASE WHEN f.source = 'epa_sems'  AND f.address IS NOT NULL AND f.address != '' THEN f.address END),
        MAX(CASE WHEN f.address IS NOT NULL AND f.address != '' THEN f.address END),
        ''
    ) AS address,
    COALESCE(
        MAX(CASE WHEN f.source || '/' || f.source_id = fm.canonical_id AND f.city IS NOT NULL AND f.city != '' THEN f.city END),
        MAX(CASE WHEN f.source = 'epa_echo'  AND f.city IS NOT NULL AND f.city != '' THEN f.city END),
        MAX(CASE WHEN f.source = 'epa_rcra'  AND f.city IS NOT NULL AND f.city != '' THEN f.city END),
        MAX(CASE WHEN f.source = 'epa_caa'   AND f.city IS NOT NULL AND f.city != '' THEN f.city END),
        MAX(CASE WHEN f.source = 'epa_sdwa'  AND f.city IS NOT NULL AND f.city != '' THEN f.city END),
        MAX(CASE WHEN f.source = 'epa_sems'  AND f.city IS NOT NULL AND f.city != '' THEN f.city END),
        MAX(CASE WHEN f.city IS NOT NULL AND f.city != '' THEN f.city END),
        ''
    ) AS city,
    MAX(f.state) AS state,
    COALESCE(MAX(CASE WHEN f.zip_code != '00000' THEN f.zip_code END), MAX(f.zip_code)) AS zip_code,
    MAX(f.county) AS county,
    COALESCE(
        MAX(CASE WHEN f.lat IS NOT NULL AND f.lon IS NOT NULL THEN f.lat END),
        MAX(f.lat)
    ) AS lat,
    COALESCE(
        MAX(CASE WHEN f.lat IS NOT NULL AND f.lon IS NOT NULL THEN f.lon END),
        MAX(f.lon)
    ) AS lon,
    GROUP_CONCAT(DISTINCT f.naics_codes) AS naics_codes,
    GROUP_CONCAT(DISTINCT f.sic_codes) AS sic_codes,
    GROUP_CONCAT(DISTINCT f.programs) AS programs,
    COUNT(DISTINCT f.source) AS source_count,
    GROUP_CONCAT(DISTINCT f.source) AS sources,
    MAX(f.last_updated) AS last_updated
FROM facilities f
JOIN facility_matches fm ON f.source = fm.source AND f.source_id = fm.source_id
GROUP BY fm.canonical_id
"""


def rebuild_unified_table(conn: sqlite3.Connection, state: str | None = None) -> int:
    """Rebuild the unified_facilities materialized table.

    Drops existing data and repopulates from facilities + facility_matches.
    Returns row count. Call after ingestion, entity resolution, or any change
    to the underlying facilities or facility_matches tables.

    If state is given, only rebuilds rows for that state (incremental).
    """
    if state:
        state = state.upper()
        # Incremental: delete this state's rows, re-insert. Use INSERT OR REPLACE
        # because cross-state facility groups (canonical_id shared across states)
        # may already exist from another state's rebuild.
        conn.execute(
            "DELETE FROM unified_facilities WHERE state = ?", (state,)
        )
        cur = conn.execute(
            f"INSERT OR REPLACE INTO unified_facilities {_UNIFIED_QUERY} HAVING MAX(f.state) = ?",
            (state,),
        )
    else:
        conn.execute("DELETE FROM unified_facilities")
        cur = conn.execute(f"INSERT INTO unified_facilities {_UNIFIED_QUERY}")
    count = cur.rowcount
    conn.commit()

    # Post-process: deduplicate programs and naics_codes within GROUP_CONCAT.
    # GROUP_CONCAT(DISTINCT) deduplicates whole strings, not individual
    # comma-separated values. Rather than fetching 5M+ rows into Python,
    # we handle this in the INSERT query itself (already uses DISTINCT).
    # Only fix remaining duplicates via Python for the small set that needs it.
    # Also catch space-separated NAICS codes (e.g. "337215 56299") that
    # GROUP_CONCAT produces when merging facilities with different codes.
    # Also catch 5-digit NAICS codes (e.g. "49311") that are missing trailing 0.
    _NAICS_SPACE_GLOB = "naics_codes GLOB '*[0-9] [0-9]*'"
    # 5-digit NAICS: any record containing a code that is exactly 5 digits.
    # Catches single-code records (e.g. "49311"), space-separated multi-code
    # records where a 5-digit code appears (e.g. "49311 56221" — also caught by
    # _NAICS_SPACE_GLOB above), and comma-separated records where one entry is
    # exactly 5 digits (e.g. "32551, 332994" or "332994, 32551").
    _NAICS_5DIGIT_GLOB = (
        "("
        "naics_codes GLOB '[0-9][0-9][0-9][0-9][0-9]' OR "
        "naics_codes GLOB '[0-9][0-9][0-9][0-9][0-9] *' OR "
        "naics_codes GLOB '[0-9][0-9][0-9][0-9][0-9],*' OR "
        "naics_codes GLOB '*,[0-9][0-9][0-9][0-9][0-9]' OR "
        "naics_codes GLOB '*,[0-9][0-9][0-9][0-9][0-9],*' OR "
        "naics_codes GLOB '*,[0-9][0-9][0-9][0-9][0-9] *' OR "
        "naics_codes GLOB '*, [0-9][0-9][0-9][0-9][0-9]' OR "
        "naics_codes GLOB '*, [0-9][0-9][0-9][0-9][0-9],*' OR "
        "naics_codes GLOB '*, [0-9][0-9][0-9][0-9][0-9] *'"
        ")"
    )
    state_filter = " AND state = ?" if state else ""
    state_params: tuple = (state,) if state else ()
    dup_count = conn.execute(
        "SELECT COUNT(*) FROM unified_facilities "
        f"WHERE (programs LIKE '%,%,%' OR naics_codes LIKE '%,%,%' OR {_NAICS_SPACE_GLOB} OR {_NAICS_5DIGIT_GLOB}){state_filter}",
        state_params,
    ).fetchone()[0]
    if dup_count > 0:
        rows = conn.execute(
            "SELECT source_id, programs, naics_codes FROM unified_facilities "
            f"WHERE (programs LIKE '%,%,%' OR naics_codes LIKE '%,%,%' OR {_NAICS_SPACE_GLOB} OR {_NAICS_5DIGIT_GLOB}){state_filter}",
            state_params,
        ).fetchall()
        updates = []
        for row in rows:
            new_progs = _dedup_csv(row[1])
            new_naics = _normalize_naics_csv(row[2])
            if new_progs != row[1] or new_naics != row[2]:
                updates.append((new_progs, new_naics, row[0]))
        if updates:
            conn.executemany(
                "UPDATE unified_facilities SET programs = ?, naics_codes = ? "
                "WHERE source_id = ?",
                updates,
            )
            conn.commit()
            logger.info("Deduplicated programs/naics for %s facilities", f"{len(updates):,}")

    # Post-process: prefer longer facility names to fix 30-char truncations.
    # EPA RCRA caps names at 30 chars. Use a single SQL UPDATE instead of
    # a correlated subquery + Python loop (was ~300s, now ~10-30s).
    # CIV-672: prefer ALL-CAPS names (government standard) over mixed-case.
    # Mixed-case names from the SAME source may be garbled duplicates (e.g.
    # GeoTracker T0603714859 "CoMilitarys"), so only use cross-source mixed-
    # case names as upgrades. Extract source prefix from unified source_id
    # (format: "source/id", e.g. "ca_geotracker/T0603700529").
    _name_state_filter = "AND state = :state" if state else ""
    name_cur = conn.execute(
        f"""
        UPDATE unified_facilities
        SET name = (
            SELECT f.name FROM facilities f
            JOIN facility_matches fm ON f.source = fm.source AND f.source_id = fm.source_id
            WHERE fm.canonical_id = unified_facilities.source_id
              AND f.name != 'Unknown'
              AND LENGTH(f.name) > 30
              AND (f.name = UPPER(f.name)
                   OR f.source != SUBSTR(unified_facilities.source_id, 1,
                        INSTR(unified_facilities.source_id, '/') - 1))
            ORDER BY
              CASE WHEN f.name = UPPER(f.name) THEN 0 ELSE 1 END,
              LENGTH(f.name) DESC
            LIMIT 1
        )
        WHERE LENGTH(name) = 30
        {_name_state_filter}
        AND EXISTS (
            SELECT 1 FROM facilities f2
            JOIN facility_matches fm2 ON f2.source = fm2.source AND f2.source_id = fm2.source_id
            WHERE fm2.canonical_id = unified_facilities.source_id
              AND f2.name != 'Unknown' AND LENGTH(f2.name) > 30
              AND (f2.name = UPPER(f2.name)
                   OR f2.source != SUBSTR(unified_facilities.source_id, 1,
                        INSTR(unified_facilities.source_id, '/') - 1))
        )
        """,
        {"state": state} if state else {},
    )
    if name_cur.rowcount:
        conn.commit()
        logger.info("Upgraded %s truncated facility names to longer variants", f"{name_cur.rowcount:,}")

    # Post-process: normalize county names (strip COUNTY/PARISH suffixes, uppercase).
    # CIV-278: sources store county inconsistently. Uses _normalize_county_storage()
    # (Python regex) because SQLite RTRIM treats its second arg as a character set,
    # not a substring — RTRIM('Harris County', ' COUNTY') strips individual chars
    # from {' ','C','O','U','N','T','Y'}, destroying the county name.
    county_rows = conn.execute(
        "SELECT source_id, county FROM unified_facilities "
        "WHERE county IS NOT NULL AND ("
        "UPPER(county) LIKE '% COUNTY' OR UPPER(county) LIKE '% PARISH' "
        "OR UPPER(county) LIKE '% BOROUGH' OR UPPER(county) LIKE '% CENSUS AREA' "
        "OR UPPER(county) LIKE '% MUNICIPALITY' "
        f"OR county != UPPER(county)){state_filter}",
        state_params,
    ).fetchall()
    county_updates = []
    for row in county_rows:
        normalized = _normalize_county_storage(row[1])
        if normalized != row[1]:
            county_updates.append((normalized, row[0]))
    if county_updates:
        conn.executemany(
            "UPDATE unified_facilities SET county = ? WHERE source_id = ?",
            county_updates,
        )
        conn.commit()
        logger.info("Normalized county names for %s facilities", f"{len(county_updates):,}")

    logger.info("Rebuilt unified_facilities: %s rows", f"{count:,}")
    return count


def _dedup_csv(val: str | None) -> str | None:
    """Deduplicate comma-separated values, preserving order.

    Also strips noise values like "NONE SPECIFIED" that some sources contribute.
    """
    if not val:
        return val
    items = list(dict.fromkeys(
        p.strip() for p in val.split(",")
        if p.strip() and p.strip().upper() != "NONE SPECIFIED"
    ))
    return ", ".join(items) if items else val


def _pad_5digit_naics(code: str) -> str:
    """Pad a 5-digit NAICS code to 6 digits by appending a trailing zero.

    Valid US NAICS codes are 6 digits. Some sources (EPA ECHO, TCEQ, RCRA)
    occasionally omit the trailing zero (e.g. '49311' instead of '493110').
    Only exactly-5-digit codes are padded; all other lengths are unchanged.
    """
    return code + "0" if len(code) == 5 else code


def _normalize_naics_csv(val: str | None) -> str | None:
    """Normalize and deduplicate NAICS codes from a GROUP_CONCAT field.

    Handles five known data quality issues that arise during multi-source merges:

    1. Exact duplicates: "812320, 812320 - Drycleaning..." → keep one
    2. Space-separated codes: "611310 622310" → split into "611310", "622310"
    3. Prefix truncation: "48411" vs "484110" → keep the longer form
    4. Placeholder code: "999999" stripped when real codes exist
    5. Truncated 5-digit codes: "49311" → "493110" (pad trailing zero)

    Each entry may be "XXXXXX" or "XXXXXX - Description". When two entries
    share the same numeric code, prefer the one with the description.
    """
    if not val:
        return val

    import re as _re

    # Step 1: Split on commas to get raw entries from GROUP_CONCAT output.
    raw_entries = [p.strip() for p in val.split(",") if p.strip()]

    # Step 2: Parse each raw entry. Some entries may be space-separated runs
    # of bare 4-7 digit NAICS codes (e.g. "611310 622310" or "325188 325320 325180").
    # Detect and expand those. A valid bare NAICS code token is 4-7 digits with
    # no " - " suffix.
    parsed: list[tuple[str, str]] = []  # list of (numeric_code, full_entry)
    for entry in raw_entries:
        # Check if this entry looks like two or more space-separated bare codes.
        # Pattern: one or more "NNNNNN" tokens separated by whitespace, no dash.
        space_run = _re.match(r'^(\d{4,7})(?:\s+\d{4,7})+$', entry)
        if space_run:
            # Split into individual entries (no description for these)
            for token in _re.split(r'\s+', entry.strip()):
                if _re.match(r'^\d{4,7}$', token):
                    padded = _pad_5digit_naics(token)
                    parsed.append((padded, padded))
        else:
            # Normal entry: extract numeric code (everything before " - " or
            # the full entry if no dash separator)
            m = _re.match(r'^(\d{4,7})\s*-', entry)
            if m:
                code = _pad_5digit_naics(m.group(1))
                # Rebuild entry with padded code if it changed
                if code != m.group(1):
                    entry = code + entry[len(m.group(1)):]
                parsed.append((code, entry))
            elif _re.match(r'^\d{4,7}$', entry.strip()):
                padded = _pad_5digit_naics(entry.strip())
                parsed.append((padded, padded))
            else:
                # Non-numeric entry (e.g. a description-only token): keep as-is
                # with empty code key so it survives but doesn't affect dedup.
                parsed.append(("", entry))

    # Step 3: Build a map from numeric_code → best entry (prefer description).
    # Also collect non-numeric entries in order.
    code_to_entry: dict[str, str] = {}
    non_code_entries: list[str] = []
    for code, entry in parsed:
        if not code:
            if entry and entry.upper() != "NONE SPECIFIED":
                non_code_entries.append(entry)
            continue
        if code == "999999":
            continue  # placeholder; handled below
        existing = code_to_entry.get(code)
        if existing is None:
            code_to_entry[code] = entry
        else:
            # Prefer entry with description over bare code
            if " - " in entry and " - " not in existing:
                code_to_entry[code] = entry

    # Step 4: Prefix normalization — if we have both "48411" and "484110",
    # keep only the longer form. For each shorter code, check if any longer
    # code is a prefix extension of it; if so, drop the shorter.
    all_codes = list(code_to_entry.keys())
    codes_to_drop: set[str] = set()
    for code_a in all_codes:
        for code_b in all_codes:
            if code_a == code_b:
                continue
            # code_a is a prefix of code_b (and code_b is longer)
            if len(code_b) > len(code_a) and code_b.startswith(code_a):
                codes_to_drop.add(code_a)

    # Step 5: Handle 999999 placeholder — add back only if no real codes remain.
    has_real_codes = any(c not in codes_to_drop for c in all_codes)
    placeholder_entries = [e for c, e in parsed if c == "999999"]

    # Step 6: Build sorted output. Sort by numeric code for consistent presentation.
    result_entries: list[str] = []
    for code in sorted(c for c in code_to_entry if c not in codes_to_drop):
        result_entries.append(code_to_entry[code])

    if not has_real_codes and placeholder_entries:
        result_entries.extend(dict.fromkeys(placeholder_entries))

    result_entries.extend(dict.fromkeys(non_code_entries))

    if not result_entries:
        return val
    return ", ".join(result_entries)


def upsert_facility(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """INSERT INTO facilities
           (source, source_id, name, address, city, state, zip_code,
            county, lat, lon, naics_codes, sic_codes, programs, last_updated)
           VALUES (:source, :source_id, :name, :address, :city, :state,
                   :zip_code, :county, :lat, :lon, :naics_codes, :sic_codes,
                   :programs, :last_updated)
           ON CONFLICT(source, source_id) DO UPDATE SET
               name        = excluded.name,
               address     = excluded.address,
               city        = excluded.city,
               state       = excluded.state,
               zip_code    = excluded.zip_code,
               county      = excluded.county,
               lat         = excluded.lat,
               lon         = excluded.lon,
               naics_codes = excluded.naics_codes,
               sic_codes   = excluded.sic_codes,
               programs    = COALESCE(excluded.programs, programs),
               last_updated = excluded.last_updated""",
        data,
    )
    # Ensure a self-match exists in facility_matches (resolve can upgrade later).
    # EPA sources use bare source_id (globally unique EPA registry IDs).
    # Non-EPA state sources use source || '/' || source_id to avoid cross-state
    # collisions (e.g. 'brownfield-108' appears in ID, KY, NJ, NM DEP/DEQ sources).
    _src = data["source"]
    _cid = (
        data["source_id"]
        if _src in _EPA_SOURCES_SET
        else f"{_src}/{data['source_id']}"
    )
    conn.execute(
        "INSERT OR IGNORE INTO facility_matches "
        "(source, source_id, canonical_id, match_tier, match_score, matched_at) "
        "VALUES (?, ?, ?, 0, 1.0, ?)",
        (_src, data["source_id"], _cid, data["last_updated"]),
    )


def upsert_violation(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO violations
           (source, source_id, facility_source_id, facility_source,
            violation_type, violation_date, statute, program_area,
            severity, status, description, last_updated)
           VALUES (:source, :source_id, :facility_source_id, :facility_source,
                   :violation_type, :violation_date, :statute, :program_area,
                   :severity, :status, :description, :last_updated)""",
        data,
    )


def cleanup_stale_ingestions(conn: sqlite3.Connection, max_age_hours: int = 6) -> int:
    """Mark orphaned 'running' pipeline_ops entries as 'failed'.

    If a process dies ungracefully (SIGKILL, OOM, container restart), the
    pipeline_ops row stays 'running' forever.  This cleans them up on startup.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    cur = conn.execute(
        "UPDATE pipeline_ops SET status = 'failed', "
        "completed_at = ?, error_message = 'stale: process died without completing' "
        "WHERE status = 'running' AND started_at < ?",
        (datetime.now(timezone.utc).isoformat(), cutoff),
    )
    count = cur.rowcount
    if count:
        conn.commit()
        logger.info("Cleaned %d orphaned 'running' pipeline_ops entries", count)
    return count


def cleanup_stale_api_usage(conn: sqlite3.Connection, max_age_days: int = 90) -> int:
    """Delete api_usage rows older than max_age_days (default 90).

    Prevents unbounded table growth on the production droplet.
    Called from init_db() so cleanup runs automatically on every startup.
    90-day retention matches the privacy policy and provides enough history
    for usage analysis.
    """
    cur = conn.execute(
        "DELETE FROM api_usage WHERE timestamp < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )
    count = cur.rowcount
    if count:
        conn.commit()
        logger.info("Deleted %d api_usage rows older than %d days", count, max_age_days)
    return count


def log_ingestion_start(
    conn: sqlite3.Connection,
    source: str,
    started_at: str,
    *,
    command_type: str = "ingest",
    triggered_by: str | None = None,
    manifest_id: str | None = None,
) -> int:
    """Log the start of a pipeline operation.

    Args:
        source: Data source name or state code (depending on command_type).
        started_at: ISO timestamp.
        command_type: ingest, score, resolve, rebuild, geocode.
        triggered_by: e.g. 'manifest-CIV-180', 'cron', 'manual'.
        manifest_id: Source manifest filename (nullable).
    """
    cur = conn.execute(
        "INSERT INTO pipeline_ops "
        "(source, started_at, status, command_type, triggered_by, manifest_id) "
        "VALUES (?, ?, 'running', ?, ?, ?)",
        (source, started_at, command_type, triggered_by, manifest_id),
    )
    conn.commit()
    return cur.lastrowid


def log_ingestion_end(
    conn: sqlite3.Connection,
    log_id: int,
    status: str,
    records: int,
    completed_at: str,
    error: str | None = None,
    *,
    details: str | None = None,
) -> None:
    """Log the completion of a pipeline operation.

    Args:
        log_id: Row ID from log_ingestion_start.
        status: 'completed' or 'failed'.
        records: Number of records processed.
        completed_at: ISO timestamp.
        error: Error message (for failed operations).
        details: JSON string with command-specific metadata.
    """
    conn.execute(
        """UPDATE pipeline_ops
           SET completed_at=?, status=?, records_processed=?, error_message=?,
               details=?
           WHERE id=?""",
        (completed_at, status, records, error, details, log_id),
    )
    conn.commit()


def run_alembic_migrations(db_path: Path | None = None) -> None:
    """Apply pending Alembic migrations programmatically.

    On a fresh database, creates all tables via the baseline migration.
    On an existing database, applies any pending migrations.
    Safe to call repeatedly — Alembic skips already-applied migrations.
    """
    import os

    from alembic import command
    from alembic.config import Config

    path = db_path or DB_PATH
    old_val = os.environ.get("ENVIOLATIONS_DB_PATH")
    os.environ["ENVIOLATIONS_DB_PATH"] = str(path)
    try:
        alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic migrations applied on %s", path)
    finally:
        if old_val is None:
            os.environ.pop("ENVIOLATIONS_DB_PATH", None)
        else:
            os.environ["ENVIOLATIONS_DB_PATH"] = old_val
