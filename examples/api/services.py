"""Shared service layer for MCP server and API routes.

Pure functions: take a sqlite3 connection, return plain dicts/lists.
No HTTP exceptions, no MCP registration — callers handle transport.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from .cfr_citations import enrich_violation
from enviolations.geo import bbox_deltas, haversine_miles, zip_centroid
from enviolations.resolve import normalize_address, _strip_trailing_directional, STATE_SOURCE_MAP


# Reverse mapping: source -> state it belongs to (for state-specific sources).
# Federal EPA sources are NOT in this map because they cover all states.
_SOURCE_STATE_MAP: dict[str, str] = STATE_SOURCE_MAP


_NOISE_PROGRAMS = {"NONE SPECIFIED", "None Specified", "none specified"}

# SQL fragment that excludes clean-status compliance records from violation counts
# and violation lists.  These are administrative EPA placeholders, not actual
# violations — storing them inflates counts and confuses non-specialists.
#   "No Violation Identified" — RCRA compliance check with no actual finding
#   "No High Priority Violation" — CAA compliance check showing clean HPV status
_NOT_CLEAN_STATUS_SQL = (
    "LOWER(COALESCE({col},'')) NOT IN "
    "('no violation identified','no high priority violation')"
)

# Violation statuses that indicate the enforcement action is no longer active.
# Used to compute active_violation_count accurately so PDF reports show correct
# "X active / Y total" counts rather than treating closed/resolved actions as active.
# Sources:
#   "resolved"    -- TCEQ, VA DEQ, and normalized sources
#   "closed"      -- NJ DEP (DOC_STATUS), raw status from various state sources
#   "withdrawn"   -- enforcement actions withdrawn before penalty is assessed
#   "superseded"  -- NJ DEP (action replaced by a newer order)
#   "voided"      -- NJ DEP (action nullified)
#   "completed"   -- some state sources use "Completed" for fully resolved actions
#   "dismissed"   -- enforcement actions dismissed without penalty
_INACTIVE_VIOLATION_STATUSES: frozenset = frozenset({
    "resolved",
    "closed",
    "withdrawn",
    "superseded",
    "voided",
    "completed",
    "dismissed",
})

# Common city name abbreviations to expand (unambiguous only — only add entries
# where the abbreviation is unmistakably a single city in context).
_CITY_ABBREVIATIONS: dict[str, str] = {
    "LA": "Los Angeles",
    "LB": "Long Beach",
    "SF": "San Francisco",
    "NYC": "New York City",
    "KC": "Kansas City",
    "OKC": "Oklahoma City",
    "NOLA": "New Orleans",
    "PHX": "Phoenix",
    "ATL": "Atlanta",
}


def _normalize_city_display(city: str | None) -> str | None:
    """Normalize city name for display: Title Case + expand common abbreviations.

    Applied at the API/service layer so the raw DB values are preserved.
    - Expands known abbreviations: "LA" -> "Los Angeles", "SF" -> "San Francisco"
    - Title-cases all-caps names: "LOS ANGELES" -> "Los Angeles"
    - Leaves already-mixed names alone (e.g. "Beverly Hills" stays as-is)
    """
    if city is None:
        return None
    city = city.strip()
    if not city:
        return None
    # Expand known abbreviations (case-insensitive lookup)
    upper = city.upper()
    if upper in _CITY_ABBREVIATIONS:
        return _CITY_ABBREVIATIONS[upper]
    # Normalize casing: if ALL CAPS or all lowercase, apply .title()
    # Mixed case (e.g. "Beverly Hills", "Los Angeles") is left unchanged
    if city == city.upper() or city == city.lower():
        return city.title()
    return city


def _clean_programs(programs: str | None) -> str:
    """Remove 'NONE SPECIFIED' and clean up programs string for display."""
    if not programs:
        return ""
    parts = [p.strip() for p in programs.split(",")]
    cleaned = [p for p in parts if p and p.upper() != "NONE SPECIFIED"]
    return ", ".join(cleaned)


def _display_name(name: str | None, address: str | None, programs: str | None = None) -> str:
    """Return a display name for a facility, falling back to address when name is missing.

    When the stored name is empty or 'Unknown', uses the address as the primary
    identifier. If programs are available, prefixes with the first program type
    (e.g. 'UST Site at 734 15th St NW'). Preserves 'Unknown' in the DB for
    provenance — this function is only called at the display/API layer.
    """
    if name and name.strip() and name.strip().lower() != "unknown":
        return name.strip()
    if address and address.strip():
        addr = address.strip()
        if programs:
            first_prog = programs.split(",")[0].strip()
            if first_prog and first_prog.upper() != "NONE SPECIFIED":
                return f"{first_prog} Site at {addr}"
        return addr
    return "Unnamed Facility"


def escape_like(value: str) -> str:
    """Escape LIKE metacharacters (%, _) in user input."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Compiled regex for PO Box detection (case-insensitive matching applied at call site)
_PO_BOX_RE = re.compile(
    r"^\s*(?:P\.?\s*O\.?\s*(?:BOX|BX)\b|POST\s+OFFICE\s+BOX\b)",
    re.IGNORECASE,
)


def _is_po_box_address(address: str | None) -> bool:
    """Return True if address is a PO Box (mailing address, not a physical location).

    PO Box coordinates are geocoded to the zip code centroid, not a real facility
    location. Including them in radius search results creates a false proximity
    signal. Examples: "PO BOX 123", "P.O. BOX 4567", "P O Box 99", "POST OFFICE BOX 1".
    """
    if not address:
        return False
    return bool(_PO_BOX_RE.match(address.strip()))


_MAX_VALID_DATE_YEAR = date.today().year + 5


def _filter_sentinel_date(d: str | None) -> str | None:
    """Return None for sentinel/placeholder dates more than 5 years in the future.

    EPA CAA and other sources use dates like 3000-12-31 as placeholders.
    These are treated as unknown rather than displayed as valid dates.
    """
    if not d:
        return None
    try:
        if int(str(d)[:4]) > _MAX_VALID_DATE_YEAR:
            return None
    except (ValueError, IndexError):
        pass
    return d


# Construction stormwater permit filtering (CIV-557).
# TCEQ "STORM" program covers both construction SWPPP (temporary, not environmentally
# significant for Phase I ESA) and industrial stormwater (ongoing, keep).
# Construction is identified by SIC 15xx-17xx (contractors) or 6552 (land developers).
_CONSTRUCTION_SIC_PREFIXES = ("15", "16", "17")
_CONSTRUCTION_SIC_EXACT = {"6552"}


def _is_construction_stormwater(item: dict) -> bool:
    """Return True if *item* is a TCEQ construction stormwater (SWPPP) permit.

    Matches TCEQ facilities whose only program is STORM and whose SIC code
    falls in the construction division (SIC 1500-1799) or land subdivision
    (SIC 6552). Industrial stormwater facilities (e.g. SIC 49xx utilities)
    are NOT matched and remain visible in search results.
    """
    if item.get("source") != "tceq":
        return False
    programs = (item.get("programs") or "").strip().upper()
    if programs != "STORM":
        return False
    sic = (item.get("sic_codes") or "").strip()
    if not sic:
        return False
    return (
        any(sic.startswith(p) for p in _CONSTRUCTION_SIC_PREFIXES)
        or sic in _CONSTRUCTION_SIC_EXACT
    )


# Shared SQL fragments used by search_radius() and search_sdwa_by_county().
# Both functions select the same columns and use the same LEFT JOINs — only
# the WHERE clause differs.  Keeping a single definition prevents the two
# queries from diverging silently when new columns are added.
_FACILITY_SELECT_COLS = (
    "SELECT f.*, "
    "  fm.canonical_id, "
    "  COALESCE(s.violation_count, 0) AS violation_count, "
    "  s.latest_violation_date, "
    "  COALESCE(s.score, -1) AS risk_score, "
    "  COALESCE(s.risk_level, 'unscored') AS risk_level, "
    "  COALESCE(s.confidence, 'low') AS confidence, "
    "  s.naics_tier, s.program_count, "
    "  uf.sources AS unified_sources, "
    "  uf.source_count, "
    "  uf.programs AS unified_programs, "
    "  uf.naics_codes AS unified_naics_codes, "
    "  uf.name AS canonical_name, "
    "  uf.address AS unified_address, "
    "  uf.city AS unified_city "
)

_FACILITY_JOINS = (
    "FROM facilities f "
    "LEFT JOIN facility_matches fm ON f.source = fm.source AND f.source_id = fm.source_id "
    "LEFT JOIN facility_scores s ON fm.canonical_id = s.source_id "
    "LEFT JOIN unified_facilities uf ON fm.canonical_id = uf.source_id "
)


def rows_to_dicts(rows) -> list[dict]:
    """Convert sqlite3 Row objects to dicts."""
    return [dict(r) for r in rows]


def enrich_violation_dict(v: dict) -> dict:
    """Add ``cfr_summary`` plain-language annotation to a single violation dict."""
    return enrich_violation(v)


def enrich_violations(violations: list[dict]) -> list[dict]:
    """Add ``cfr_summary`` plain-language annotation to each violation dict.

    Also filters sentinel violation_date values (e.g. 3000-12-31) to None.
    """
    for v in violations:
        enrich_violation(v)
        v["violation_date"] = _filter_sentinel_date(v.get("violation_date"))
    return violations


def parse_since(since: str) -> str:
    """Parse '2y', '6m', '90d', '1w', or ISO date -> ISO date string.

    Raises ValueError on bad input (callers convert to HTTPException/error dict).
    """
    m = re.match(r"^(\d+)([ymwd])$", since.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"y": 365, "m": 30, "w": 7, "d": 1}[unit]
        return (date.today() - timedelta(days=n * days)).isoformat()
    try:
        date.fromisoformat(since)
        return since
    except ValueError:
        raise ValueError(
            f"Invalid 'since' format: {since}. Use ISO date (2024-01-01) or relative (2y, 6m, 90d, 1w)"
        )


def parse_lat_lon(text: str) -> tuple[float, float] | None:
    """Try to parse 'lat,lon' from a string. Returns None if not coordinates."""
    parts = text.strip().split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return (lat, lon)
        except ValueError:
            pass
    return None


def search_facilities_by_filter(
    conn,
    *,
    query: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    county: str | None = None,
    program: str | None = None,
    limit: int = 100,
) -> dict | None:
    """Filter-based facility search with scores.

    Returns {total, facilities} or None if no filters provided.
    """
    clauses: list[str] = []
    params: list = []

    if query:
        clauses.append("f.name LIKE ? ESCAPE '\\'")
        params.append(f"%{escape_like(query)}%")
    if state:
        clauses.append("f.state = ?")
        params.append(state.upper())
    if zip_code:
        clauses.append("f.zip_code = ?")
        params.append(zip_code)
    if county:
        clauses.append("f.county LIKE ? ESCAPE '\\'")
        params.append(f"%{escape_like(county)}%")
    if program:
        clauses.append("f.programs LIKE ? ESCAPE '\\'")
        params.append(f"%{escape_like(program)}%")

    if not clauses:
        return None

    where = f"WHERE {' AND '.join(clauses)}"

    total = conn.execute(
        f"SELECT COUNT(*) FROM facilities f {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT f.*, "
        "  fm.canonical_id, "
        "  COALESCE(s.violation_count, 0) AS violation_count, "  # pre-computed by scoring
        "  s.latest_violation_date, "  # pre-computed by scoring
        "  COALESCE(s.score, -1) AS risk_score, "
        "  COALESCE(s.risk_level, 'unscored') AS risk_level, "
        "  COALESCE(s.confidence, 'low') AS confidence "
        "FROM facilities f "
        "LEFT JOIN facility_matches fm ON f.source = fm.source AND f.source_id = fm.source_id "
        "LEFT JOIN facility_scores s ON fm.canonical_id = s.source_id "
        f"{where} LIMIT ?",
        params + [limit],
    ).fetchall()

    facilities = rows_to_dicts(rows)
    for fac in facilities:
        fac["latest_violation_date"] = _filter_sentinel_date(fac.get("latest_violation_date"))
        if fac.get("risk_score") == -1:
            fac["risk_score"] = None
    return {
        "total": total,
        "facilities": facilities,
    }


def get_facility_detail(conn, source: str, source_id: str) -> dict | None:
    """Single facility with violations, score, source citation. None if not found."""
    row = conn.execute(
        "SELECT * FROM facilities WHERE source = ? AND source_id = ?",
        (source, source_id),
    ).fetchone()
    if not row:
        return None

    facility = dict(row)

    # Normalize city casing for display (Title Case + abbreviation expansion)
    facility["city"] = _normalize_city_display(facility.get("city"))

    # Violations — query through facility_matches so cross-source violations
    # (e.g. CA Waterboard violations for an EPA ECHO facility) are included.
    # Exclude clean inspection records such as RCRA "No Violation Identified"
    # and CAA "No High Priority Violation" which are not actual violations.
    _vtype_filter = _NOT_CLEAN_STATUS_SQL.format(col="v.violation_type")
    canonical_row = conn.execute(
        "SELECT canonical_id FROM facility_matches WHERE source = ? AND source_id = ?",
        (source, source_id),
    ).fetchone()
    if canonical_row:
        canonical_id = canonical_row["canonical_id"]
        violations = conn.execute(
            "SELECT v.* FROM violations v "
            "JOIN facility_matches fm ON v.facility_source = fm.source "
            "  AND v.facility_source_id = fm.source_id "
            "WHERE fm.canonical_id = ? "
            f"AND {_vtype_filter} "
            "ORDER BY v.violation_date DESC LIMIT 50",
            (canonical_id,),
        ).fetchall()
        facility["violation_count"] = conn.execute(
            "SELECT COUNT(*) FROM violations v "
            "JOIN facility_matches fm ON v.facility_source = fm.source "
            "  AND v.facility_source_id = fm.source_id "
            "WHERE fm.canonical_id = ? "
            f"AND {_vtype_filter}",
            (canonical_id,),
        ).fetchone()[0]
    else:
        # Fallback: facility has no facility_matches entry — query directly
        _vtype_filter_direct = _NOT_CLEAN_STATUS_SQL.format(col="violation_type")
        violations = conn.execute(
            "SELECT * FROM violations "
            "WHERE facility_source = ? AND facility_source_id = ? "
            f"AND {_vtype_filter_direct} "
            "ORDER BY violation_date DESC LIMIT 50",
            (source, source_id),
        ).fetchall()
        facility["violation_count"] = conn.execute(
            "SELECT COUNT(*) FROM violations "
            "WHERE facility_source = ? AND facility_source_id = ? "
            f"AND {_vtype_filter_direct}",
            (source, source_id),
        ).fetchone()[0]
    facility["violations"] = enrich_violations(rows_to_dicts(violations))

    # Risk score (join through facility_matches)
    score_row = conn.execute(
        "SELECT s.* FROM facility_matches fm "
        "JOIN facility_scores s ON fm.canonical_id = s.source_id "
        "WHERE fm.source = ? AND fm.source_id = ?",
        (source, source_id),
    ).fetchone()
    if score_row:
        facility["risk_score"] = dict(score_row)

    # Source citation
    last_ingest = conn.execute(
        "SELECT MAX(completed_at) FROM pipeline_ops WHERE source = ? AND status = 'completed'",
        (source,),
    ).fetchone()[0]
    facility["source_citation"] = {
        "source": source,
        "last_ingested": last_ingest,
    }

    return facility


def _filter_po_boxes(items: list[dict]) -> list[dict]:
    """Filter 1: Remove facilities with PO Box mailing addresses (CIV-559).

    PO Box coordinates are geocoded to the zip code centroid, not a real physical
    location. Including them in radius search results creates a false proximity
    signal that misleads Phase I ESA users.
    """
    return [item for item in items if not _is_po_box_address(item.get("address"))]


def _filter_coord_mismatch(items: list[dict]) -> list[dict]:
    """Filter 2: Remove facilities whose stored coordinates don't match their zip code (CIV-156/CIV-246).

    If a facility's zip centroid is more than 5 miles from its stored lat/lon, the
    source data has wrong coordinates. Examples: WATSONVILLE JUNCTION (zip 95076,
    ~70mi from stored Oakland coords); BAPKO METAL (zip 90067 Century City, ~9mi
    from stored downtown-LA coords). 5 miles catches intra-city misgeocodes while
    allowing for large zip codes.
    """
    result = []
    for item in items:
        fac_zip = (item.get("zip_code") or "").strip()[:5]
        if fac_zip and len(fac_zip) == 5 and fac_zip.isdigit():
            centroid = zip_centroid(fac_zip, api_fallback=False)
            if centroid is not None:
                zip_dist = haversine_miles(item["lat"], item["lon"], centroid[0], centroid[1])
                if zip_dist > 5.0:
                    continue
        result.append(item)
    return result


def _filter_hq_geocoded(items: list[dict]) -> list[dict]:
    """Filter 2b: Remove facilities whose coordinates are likely a corporate HQ, not
    the physical facility location.

    EPA PFAS and similar sources sometimes only have the parent company's HQ
    coordinates.  Telltale pattern: no street address, no zip code, and multiple
    facilities sharing the exact same coordinate pair within the result set.
    """
    # Count how many results share each exact coordinate pair
    coord_counts: dict[tuple[float, float], int] = {}
    for item in items:
        coord = (item.get("lat"), item.get("lon"))
        if coord[0] is not None:
            coord_counts[coord] = coord_counts.get(coord, 0) + 1

    result = []
    for item in items:
        addr = (item.get("address") or "").strip()
        zip_code = (item.get("zip_code") or "").strip()
        coord = (item.get("lat"), item.get("lon"))
        shared = coord_counts.get(coord, 0)
        # HQ-geocoded: no address, no zip, and 3+ facilities at identical coords
        if not addr and not zip_code and shared >= 3:
            continue
        result.append(item)
    return result


def _filter_construction_stormwater(items: list[dict]) -> list[dict]:
    """Filter 3: Remove TCEQ construction stormwater (SWPPP) permits with no violations (CIV-557).

    These are temporary construction permits that are not environmentally significant
    for Phase I ESA purposes. If a construction site has actual violations it remains
    visible.
    """
    return [
        item for item in items
        if not ((item.get("violation_count") or 0) == 0 and _is_construction_stormwater(item))
    ]


def _apply_sentinel_dates(items: list[dict]) -> list[dict]:
    """Filter 4: Replace sentinel/placeholder values with None.

    - Dates: EPA CAA and other sources use future dates (e.g. 3000-12-31) as placeholders.
    - Scores: COALESCE(s.score, -1) produces -1 for unscored facilities. Convert to None
      so consumers (MCP, stress test) never see the internal sentinel. CIV-537.
    """
    for item in items:
        item["latest_violation_date"] = _filter_sentinel_date(item.get("latest_violation_date"))
        if item.get("risk_score") == -1:
            item["risk_score"] = None
    return items


def _filter_canonical_dedup(items: list[dict]) -> list[dict]:
    """Filter 5: Deduplicate by canonical_id, keeping the closest facility per canonical group.

    Items must already be sorted by distance_miles ascending so the first occurrence
    is the closest record.
    """
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        cid = item.get("canonical_id") or item["source_id"]
        if cid not in seen:
            seen.add(cid)
            result.append(item)
    return result


def _filter_name_addr_dedup(items: list[dict]) -> list[dict]:
    """Filter 6: Secondary dedup — same real name+address across different canonical groups.

    Uses raw DB name (before _display_name transform) stored in item["_raw_name"] so
    that facilities with "Unknown"/empty names at the same address are never wrongly
    collapsed. Uses normalize_address() + trailing-directional stripping so that
    variants like "2100 LOUISIANA BLVD NE" and "2100 Louisiana Blvd." deduplicate
    correctly when entity resolution hasn't merged them yet.
    """
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for item in items:
        raw = item["_raw_name"].upper().strip()
        addr = _strip_trailing_directional(normalize_address(item.get("address")))
        if not raw or raw == "UNKNOWN":
            result.append(item)
        else:
            key = (raw, addr)
            if key not in seen:
                seen.add(key)
                result.append(item)
    return result


def _filter_geo_name_dedup(items: list[dict]) -> list[dict]:
    """Filter 7: Geo-proximity name dedup — safety net for entity resolution gaps.

    Catches near-duplicates that survived canonical and name+address dedup:
    facilities within ~200m with high name similarity.  This covers large sources
    (>100K records) that skip the O(n²) entity resolution pass, and cases where
    entity resolution created multiple canonical groups for what should be one.

    Two proximity tiers (both require the accepted facility to have an address):
      - Close range (~67m, 0.0006° lat/lon): fuzz.ratio >= 90 — facilities at
        essentially the same GPS point; tight name check avoids collapsing
        adjacent different businesses.
      - Medium range (~200m, 0.002° lat/lon): fuzz.ratio >= 83 — catches sources
        like fl_dep WAFR permits whose coordinates can be 100-200m from the
        EPA-registered site centroid (CIV-722). The slightly looser name threshold
        covers cases like "Acme Corp" vs "Acme Corp Inc - City" (same facility,
        different name variants from different permit systems).

    Items are already sorted by distance (closest first), so we greedily keep the
    first facility and skip later ones that match any already-accepted facility.
    """
    if len(items) < 2:
        return items

    from rapidfuzz import fuzz

    _GENERIC = {"UNNAMED FACILITY", "UNKNOWN", ""}

    result: list[dict] = []
    for item in items:
        lat, lon = item.get("lat"), item.get("lon")
        name = (item.get("name") or "").upper().strip()

        # Always keep items without coords or with generic names
        if not lat or not lon or name in _GENERIC:
            result.append(item)
            continue

        is_dupe = False
        for accepted in result:
            alat, alon = accepted.get("lat"), accepted.get("lon")
            if not alat or not alon:
                continue
            aname = (accepted.get("name") or "").upper().strip()
            if aname in _GENERIC:
                continue

            dlat = abs(lat - alat)
            dlon = abs(lon - alon)

            # Quick outer bounding-box filter (~200m ≈ 0.002° lat/lon)
            if dlat > 0.002 or dlon > 0.002:
                continue

            ratio = fuzz.ratio(name, aname)

            # Close range (~67m): require 90% similarity
            if dlat <= 0.0006 and dlon <= 0.0008:
                if ratio >= 90:
                    is_dupe = True
                    break
            # Medium range (~200m): require 83% similarity — catches sources
            # whose coordinates can be 100-200m from the EPA site centroid
            elif ratio >= 83:
                is_dupe = True
                break

        if not is_dupe:
            result.append(item)

    return result


def _filter_state_mismatch(items: list[dict], center_state: str) -> list[dict]:
    """Filter 8: Remove facilities whose registered state doesn't match the search center state.

    These are typically offshore/pipeline operations whose EPA coordinates point to a
    HQ address in the search city but are registered in another state. Only filters
    facilities with a valid 2-letter state code that differs from center_state.
    """
    cs = center_state.upper()
    result: list[dict] = []
    for item in items:
        fac_state = (item.get("state") or "").strip().upper()
        if fac_state and len(fac_state) == 2 and fac_state != cs:
            item["state_mismatch"] = True
        else:
            result.append(item)
    return result


def search_radius(
    conn,
    center_lat: float,
    center_lon: float,
    radius_miles: float,
    *,
    limit: int | None = None,
    center_state: str | None = None,
) -> list[dict]:
    """Bbox + haversine with scores + canonical dedup. Sorted by distance.

    Filter pipeline (in order):
      1. _filter_po_boxes            — exclude PO Box mailing addresses
      2. _filter_coord_mismatch      — exclude facilities with wrong coordinates
      2b. _filter_hq_geocoded        — exclude HQ-geocoded phantoms (no addr/zip + shared coords)
      3. _filter_construction_stormwater — exclude no-violation TCEQ SWPPP permits
      4. _apply_sentinel_dates       — replace placeholder dates with None
      5. _filter_canonical_dedup     — keep closest per canonical group
      6. _filter_name_addr_dedup     — secondary name+address dedup
      7. _filter_geo_name_dedup      — geo proximity + name similarity dedup
      8. _filter_state_mismatch      — exclude cross-state facilities (when center_state given)
    """
    delta_lat, delta_lon = bbox_deltas(center_lat, radius_miles)

    rows = conn.execute(
        _FACILITY_SELECT_COLS
        + _FACILITY_JOINS
        + "WHERE f.lat BETWEEN ? AND ? AND f.lon BETWEEN ? AND ?",
        (
            center_lat - delta_lat,
            center_lat + delta_lat,
            center_lon - delta_lon,
            center_lon + delta_lon,
        ),
    ).fetchall()

    # Build initial results: distance filter + row normalization
    results = []
    for row in rows:
        d = haversine_miles(center_lat, center_lon, row["lat"], row["lon"])
        if d > radius_miles:
            continue
        item = dict(row)
        item["distance_miles"] = round(d, 3)
        # Use aggregated programs/naics from unified table when available
        # (raw f.programs only has one source's programs after dedup)
        if item.get("unified_programs"):
            item["programs"] = item["unified_programs"]
        if item.get("unified_naics_codes"):
            item["naics_codes"] = item["unified_naics_codes"]
        # Use canonical name from unified_facilities (source-priority-ordered) so
        # search card name matches what the detail drawer shows.
        if item.get("canonical_name"):
            item["name"] = item["canonical_name"]
        # Use canonical address/city from unified_facilities when available.
        # unified_facilities.address uses source-priority ordering (canonical
        # record first, then epa_echo, epa_rcra, epa_caa, etc.) so that a
        # corrupted address from a non-canonical matched record (e.g. "SMA
        # AMTEO NE" from epa_caa) is replaced by the correct address from
        # the primary record (e.g. "SAN MATEO BLVD NE" from epa_echo).
        # Without this override the raw f.address from whichever facilities
        # row was returned first could be the corrupted variant (CIV-525).
        if item.get("unified_address"):
            item["address"] = item["unified_address"]
        if item.get("unified_city"):
            item["city"] = item["unified_city"]
        # Clean noise from programs display
        item["programs"] = _clean_programs(item.get("programs"))
        # Normalize city casing for display (Title Case + abbreviation expansion)
        item["city"] = _normalize_city_display(item.get("city"))
        # Preserve raw name (before _display_name transform) for secondary dedup.
        item["_raw_name"] = item.get("name") or ""
        results.append(item)

    # Sort by distance, then by source_id as a stable tiebreaker.
    # Without the tiebreaker, facilities at identical rounded distances are ordered
    # by SQLite's non-deterministic row scan order.  When _filter_canonical_dedup
    # keeps the *first* occurrence per canonical group, a different row ordering
    # produces a different "winner" — causing the final count to vary between
    # identical searches (CIV-761).
    results.sort(key=lambda x: (x["distance_miles"], x.get("source_id") or ""))

    # Apply filter pipeline
    results = _filter_po_boxes(results)
    results = _filter_coord_mismatch(results)
    results = _filter_hq_geocoded(results)
    results = _filter_construction_stormwater(results)
    results = _apply_sentinel_dates(results)
    results = _filter_canonical_dedup(results)
    results = _filter_name_addr_dedup(results)
    results = _filter_geo_name_dedup(results)

    # Apply display name fallback: unnamed facilities use address as identifier.
    # Runs after dedup so the key uses the raw name, not the transformed one.
    for item in results:
        item["name"] = _display_name(item["_raw_name"] or None, item.get("address"), item.get("programs"))
        del item["_raw_name"]

    if center_state:
        results = _filter_state_mismatch(results, center_state)

    if limit is not None:
        return results[:limit]
    return results


def fetch_canonical_violations(
    conn, canonical_ids: list[str]
) -> dict[str, list[dict]]:
    """Batch-fetch violations grouped by canonical_id. Batches of 500."""
    result: dict[str, list[dict]] = {}
    batch_size = 500
    for i in range(0, len(canonical_ids), batch_size):
        batch = canonical_ids[i : i + batch_size]
        placeholders = ",".join("?" * len(batch))
        vrows = conn.execute(
            f"SELECT fm.canonical_id, v.* FROM violations v "
            f"JOIN facility_matches fm ON v.facility_source = fm.source "
            f"  AND v.facility_source_id = fm.source_id "
            f"WHERE fm.canonical_id IN ({placeholders}) "
            f"AND {_NOT_CLEAN_STATUS_SQL.format(col='v.violation_type')} "
            f"ORDER BY v.violation_date DESC",
            batch,
        ).fetchall()
        for vrow in vrows:
            vdict = dict(vrow)
            cid = vdict.pop("canonical_id")
            enrich_violation(vdict)
            result.setdefault(cid, []).append(vdict)
    return result


def enrich_search_violation_counts(conn, facilities: list[dict]) -> None:
    """Update violation_count and latest_violation_date in-place using live DB queries.

    The search_radius query uses pre-computed facility_scores.violation_count which
    may be stale (e.g. scored before a cross-source match was created, or before
    new violations were ingested from a matched source). This function re-computes
    counts by querying violations via facility_matches so that cross-source violations
    are always included in the W/ Violations stat and CSV export (CIV-635).

    Operates in batches of 900 to stay within SQLite's variable limit.
    """
    if not facilities:
        return

    # Build canonical_id → facility dict mapping (some facilities may share canonical_id
    # after dedup; update all of them)
    cid_to_facs: dict[str, list[dict]] = {}
    for fac in facilities:
        cid = fac.get("canonical_id") or fac.get("source_id")
        if cid:
            cid_to_facs.setdefault(cid, []).append(fac)

    canonical_ids = list(cid_to_facs.keys())
    batch_size = 900
    not_clean_sql = _NOT_CLEAN_STATUS_SQL.format(col="v.violation_type")
    for i in range(0, len(canonical_ids), batch_size):
        batch = canonical_ids[i : i + batch_size]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT fm.canonical_id, "
            f"  COUNT(*) AS violation_count, "
            f"  MAX(v.violation_date) AS latest_violation_date "
            f"FROM violations v "
            f"JOIN facility_matches fm ON v.facility_source = fm.source "
            f"  AND v.facility_source_id = fm.source_id "
            f"WHERE fm.canonical_id IN ({placeholders}) "
            f"AND {not_clean_sql} "
            f"GROUP BY fm.canonical_id",
            batch,
        ).fetchall()
        counts: dict[str, tuple[int, str | None]] = {
            r["canonical_id"]: (r["violation_count"], r["latest_violation_date"])
            for r in rows
        }
        for cid in batch:
            vc, lvd = counts.get(cid, (0, None))
            lvd = _filter_sentinel_date(lvd)
            for fac in cid_to_facs.get(cid, []):
                fac["violation_count"] = vc
                fac["latest_violation_date"] = lvd


def search_sdwa_by_county(
    conn,
    state: str,
    county: str,
    center_lat: float,
    center_lon: float,
) -> list[dict]:
    """Return SDWA water system facilities in a county, with scores and distance.

    SDWA water systems serve geographic areas (cities/counties) but their
    stored coordinates are at their treatment plant or office, which may be
    far from the service area.  This function queries by county so that a
    community water system serving a county appears in searches for any
    point in that county, regardless of where the treatment plant is.

    Returns the same field set as search_radius(), with distance_miles computed
    from the facility's stored coordinates to the search center.  Facilities
    with no coordinates get distance_miles=None.
    """
    rows = conn.execute(
        _FACILITY_SELECT_COLS
        + _FACILITY_JOINS
        + "WHERE f.source = 'epa_sdwa' AND f.state = ? AND UPPER(f.county) = UPPER(?)",
        (state, county),
    ).fetchall()

    results = []
    for row in rows:
        item = dict(row)
        if item.get("lat") is not None and item.get("lon") is not None:
            item["distance_miles"] = round(
                haversine_miles(center_lat, center_lon, item["lat"], item["lon"]), 3
            )
        else:
            item["distance_miles"] = None
        item["latest_violation_date"] = _filter_sentinel_date(item.get("latest_violation_date"))
        if item.get("unified_programs"):
            item["programs"] = item["unified_programs"]
        if item.get("unified_naics_codes"):
            item["naics_codes"] = item["unified_naics_codes"]
        if item.get("canonical_name"):
            item["name"] = item["canonical_name"]
        if item.get("unified_address"):
            item["address"] = item["unified_address"]
        if item.get("unified_city"):
            item["city"] = item["unified_city"]
        item["programs"] = _clean_programs(item.get("programs"))
        item["city"] = _normalize_city_display(item.get("city"))
        raw_name = item.get("name") or ""
        item["name"] = _display_name(raw_name or None, item.get("address"), item.get("programs"))
        item["sdwa_county_match"] = True
        results.append(item)

    results.sort(key=lambda x: (-(x.get("violation_count") or 0), x.get("name") or ""))
    return results


def get_all_active_sources(conn) -> list[str]:
    """Return sorted list of all distinct sources present in the facilities table."""
    rows = conn.execute(
        "SELECT DISTINCT source FROM facilities ORDER BY source"
    ).fetchall()
    return [r[0] for r in rows]


def fetch_source_citations(
    conn,
    sources: set[str],
    facility_counts: dict[str, int] | None = None,
) -> list[dict]:
    """Batch-fetch ingestion dates. Optional per-source facility counts."""
    src_list = sorted(sources)
    if not src_list:
        return []
    placeholders = ",".join("?" * len(src_list))
    ingest_rows = conn.execute(
        f"SELECT source, MAX(completed_at) FROM pipeline_ops "
        f"WHERE source IN ({placeholders}) AND status = 'completed' GROUP BY source",
        src_list,
    ).fetchall()
    ingest_map = {r[0]: r[1] for r in ingest_rows}
    result = []
    for src in src_list:
        entry: dict = {
            "source": src,
            "last_retrieved": ingest_map.get(src),
        }
        if facility_counts is not None:
            entry["facility_count_in_radius"] = facility_counts.get(src, 0)
        result.append(entry)
    return result


def build_screening_report(
    conn,
    center_lat: float,
    center_lon: float,
    radius_miles: float,
    resolved_address: str | None = None,
    input_address: str | None = None,
) -> dict:
    """Full screening report: radius search -> violations -> citations -> summary.

    Calls search_radius, fetch_canonical_violations, fetch_source_citations internally.

    resolved_address is the geocoded/normalized address used for state extraction.
    input_address is the user's original typed address; shown in PDF/report headers.
    """
    # Extract center state for state_mismatch flagging.
    # Mirror the same fallback logic used by the /search route: try _extract_state
    # first, then fall back to reverse_geocode_state so both code paths apply
    # identical state-mismatch filtering and return the same facility count.
    center_state = None
    if resolved_address:
        from .geo import _extract_state
        center_state = _extract_state(resolved_address)
    if center_state is None:
        from .geo import reverse_geocode_state
        center_state = reverse_geocode_state(center_lat, center_lon)
    facilities = search_radius(
        conn, center_lat, center_lon, radius_miles, center_state=center_state,
    )

    # Collect canonical IDs and source set (include all unified sources)
    source_set: set[str] = set()
    cid_list: list[str] = []
    for fac in facilities:
        source_set.add(fac["source"])
        # Also include all sources from the unified record
        for s in (fac.get("unified_sources") or "").split(","):
            s = s.strip()
            if s:
                source_set.add(s)
        cid = fac.get("canonical_id") or fac["source_id"]
        cid_list.append(cid)
        fac["violations"] = []
        fac["violation_count"] = 0

    # Fetch and attach violations
    vio_by_cid = fetch_canonical_violations(conn, cid_list)
    all_violations: list[dict] = []
    for fac in facilities:
        cid = fac.get("canonical_id") or fac["source_id"]
        viols = vio_by_cid.get(cid, [])
        fac["violations"] = viols
        fac["violation_count"] = len(viols)
        fac["active_violation_count"] = sum(
            1 for v in viols
            if (v.get("status") or "").strip().lower() not in _INACTIVE_VIOLATION_STATUSES
        )
        all_violations.extend(viols)

    # Source citations — always include ALL active sources (not just those with results).
    # This mirrors EDR-style reports where every searched database is listed regardless
    # of whether it returned results. A zero-result report must still document search scope.
    all_active_sources = get_all_active_sources(conn)
    all_source_set: set[str] = set(all_active_sources) | source_set

    # Count each facility against all its unified sources.
    # Guard against cross-state source inflation: a unified cluster may include
    # state-specific sources from another state (a legacy of pre-CIV-460 canonical_id
    # collisions where e.g. NM and KY facilities shared the same bare source_id).
    # Only credit a state-specific source if its state matches the facility's state.
    # Federal EPA sources (epa_echo, epa_rcra, etc.) have no state restriction.
    src_counts: dict[str, int] = {}
    for fac in facilities:
        fac_state = (fac.get("state") or "").strip().upper()
        all_srcs = set()
        all_srcs.add(fac["source"])
        for s in (fac.get("unified_sources") or "").split(","):
            s = s.strip()
            if s:
                all_srcs.add(s)
        for s in all_srcs:
            # Skip state-specific source if its state doesn't match this facility's state
            src_state = _SOURCE_STATE_MAP.get(s)
            if src_state and fac_state and src_state != fac_state:
                continue
            src_counts[s] = src_counts.get(s, 0) + 1
    sources_info = fetch_source_citations(conn, all_source_set, src_counts)

    # Risk summary — use case-insensitive comparison to handle any casing in the DB
    critical = sum(1 for f in facilities if (f.get("risk_level") or "").lower() == "critical")
    high = sum(1 for f in facilities if (f.get("risk_level") or "").lower() == "high")
    medium = sum(1 for f in facilities if (f.get("risk_level") or "").lower() == "medium")
    low = sum(1 for f in facilities if (f.get("risk_level") or "").lower() == "low")

    site: dict = {
        "address": resolved_address,
        "lat": center_lat,
        "lon": center_lon,
        "search_radius_miles": radius_miles,
    }
    if input_address:
        site["input_address"] = input_address

    return {
        "report_type": "Environmental Screening Report - Public Records Search",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "site": site,
        "summary": {
            "total_facilities": len(facilities),
            "total_violations": len(all_violations),
            "risk_breakdown": {"critical": critical, "high": high, "medium": medium, "low": low},
            "sources_queried": len(sources_info),
        },
        "facilities": facilities,
        "source_citations": sources_info,
    }


def get_coverage_stats(conn) -> dict:
    """Dataset coverage: totals, per-source breakdown, unified counts."""
    fac = conn.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]
    vio = conn.execute("SELECT COUNT(*) FROM violations").fetchone()[0]
    states = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT state FROM facilities WHERE state IS NOT NULL ORDER BY state"
        ).fetchall()
    ]

    source_rows = conn.execute(
        "SELECT f.source, COUNT(*) AS facility_count, "
        "  COALESCE(v.violation_count, 0) AS violation_count, "
        "  il.last_ingest "
        "FROM facilities f "
        "LEFT JOIN ("
        "  SELECT facility_source AS source, COUNT(*) AS violation_count "
        "  FROM violations GROUP BY facility_source"
        ") v ON f.source = v.source "
        "LEFT JOIN ("
        "  SELECT source, MAX(completed_at) AS last_ingest "
        "  FROM pipeline_ops WHERE status = 'completed' GROUP BY source"
        ") il ON f.source = il.source "
        "GROUP BY f.source ORDER BY COUNT(*) DESC"
    ).fetchall()

    sources = [
        {
            "name": r["source"],
            "facility_count": r["facility_count"],
            "violation_count": r["violation_count"],
            "last_ingest": r["last_ingest"],
        }
        for r in source_rows
    ]

    unified_fac = conn.execute(
        "SELECT COUNT(*) FROM unified_facilities"
    ).fetchone()[0]
    scored = conn.execute("SELECT COUNT(*) FROM facility_scores").fetchone()[0]

    return {
        "total_facilities": fac,
        "total_violations": vio,
        "total_unified_facilities": unified_fac,
        "total_scored": scored,
        "states_covered": len(states),
        "states": states,
        "sources": sources,
        "source_count": len(sources),
    }
