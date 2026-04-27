"""API route definitions."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from datetime import date, datetime, timezone
from urllib.parse import quote as _url_quote
from xml.sax.saxutils import escape as _xml_escape

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from starlette.responses import FileResponse, StreamingResponse

logger = logging.getLogger(__name__)

from . import services
from enviolations.db import get_connection
from .services import _NOT_CLEAN_STATUS_SQL
from enviolations.geo import geocode_address as _geocode_address, _extract_state, detect_road_type_substitution as _detect_road_type_substitution, reverse_geocode_city_county as _reverse_geocode_city_county
from .auth import require_api_key
from .cache import get_cache
from .pdf import (
    SOURCE_DISPLAY_NAMES,
    PROGRAM_DISPLAY_NAMES,
    _program_display_name,
    _source_display_name,
    _source_url,
    _pdf_title_case,
    _pdf_humanize_programs,
    _violation_recency_label,
    _violation_status_label,
    generate_map_image,
    generate_screening_pdf,
)

# Backward-compatible aliases for tests and internal callers.
_generate_map_image = generate_map_image
_generate_screening_pdf = generate_screening_pdf

router = APIRouter(dependencies=[Depends(require_api_key)])

# Public router — no auth required. Only for lightweight liveness probes.
public_router = APIRouter()


def _get_db():
    """FastAPI dependency that yields a DB connection and ensures cleanup."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _paginated_response(
    request: Request, items: list, total: int, limit: int, offset: int, **extra
) -> dict:
    """Build a paginated response with next/previous links."""
    result = {"items": items, "total": total, "limit": limit, "offset": offset}
    result.update(extra)
    if offset + limit < total:
        result["next"] = str(
            request.url.include_query_params(offset=offset + limit, limit=limit)
        )
    else:
        result["next"] = None
    if offset > 0:
        prev_offset = max(0, offset - limit)
        result["previous"] = str(
            request.url.include_query_params(offset=prev_offset, limit=limit)
        )
    else:
        result["previous"] = None
    return result


def _parse_since(since: str) -> str:
    """Parse a relative duration or ISO date, raising HTTPException on failure."""
    try:
        return services.parse_since(since)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _geocode_error_detail(address: str) -> dict:
    """Build a structured 422 error detail for a geocoding failure.

    Returns a dict with a human-readable message and actionable suggestions
    so the frontend can display helpful guidance instead of a bare error.
    """
    suggestions = []

    # Suggest nearby intersection or landmark
    suggestions.append("Try a nearby intersection or landmark (e.g. Hwy 519 & Miller Cut Off Rd)")

    # Suggest searching by city/zip extracted from the address
    parts = [p.strip() for p in address.split(",")]
    city_zip_parts = []
    if len(parts) >= 2:
        city_zip_parts.append(parts[1].strip())  # city
    if len(parts) >= 3:
        city_zip_parts.append(parts[2].strip())  # state + zip
    if city_zip_parts:
        suggestions.append(f"Search by city/zip instead: {', '.join(city_zip_parts)}")
    else:
        state = _extract_state(address)
        if state:
            suggestions.append(f"Search by city or zip code in {state}")

    # Suggest lat/lon coordinate input
    suggestions.append(
        "Enter GPS coordinates directly (e.g. 29.376, -94.912)"
    )

    return {
        "message": "Could not geocode address — this road may not be in public geocoding databases.",
        "suggestions": suggestions,
    }


_CSV_DISCLAIMER = (
    "# Screening data - Not for ASTM E1527-21 Compliance\n"
    "# This data is for informational screening purposes only. It does not constitute\n"
    "# an ASTM E1527-21 compliant environmental database report.\n"
    "# Aggregated from public US federal and state environmental agency sources.\n"
)




def _humanize_programs(programs: str, state: str | None = None) -> str:
    """Apply human-readable display names to program codes for PDF/CSV export."""
    if not programs:
        return ""
    parts = [p.strip() for p in programs.split(",")]
    return ", ".join(
        _program_display_name(p.upper(), state) or p
        for p in parts
        if p and p.upper() != "NONE SPECIFIED"
    )



# Internal DB / dedup columns that are not meaningful to Phase I consultants.
# Excluded from the facilities and search CSV exports (CIV-512).
_CSV_INTERNAL_COLUMNS = frozenset({
    "canonical_id",
    "unified_sources",
    "source_count",
    "unified_programs",
    "unified_naics_codes",
    "naics_tier",
    "program_count",
    "_raw_name",
    "canonical_name",
})


def _clean_facility_for_csv(row: dict) -> dict:
    """Return a consultant-friendly dict for facility CSV export (CIV-512).

    - Applies human-readable program names (NJEMS → NJ DEP Site Registry, etc.)
    - Truncates ISO timestamps in last_updated to YYYY-MM-DD
    - Drops internal dedup columns not meaningful to Phase I consultants
    - Converts -1 sentinel risk_score (unscored) to None/blank (CIV-537)
    """
    out = {k: v for k, v in row.items() if k not in _CSV_INTERNAL_COLUMNS}
    if "programs" in out:
        out["programs"] = _humanize_programs(out.get("programs") or "", state=row.get("state"))
    if "last_updated" in out and out["last_updated"]:
        ts = str(out["last_updated"])
        # Accept ISO timestamps like "2026-03-10T13:58:07.455671Z" or plain dates
        out["last_updated"] = ts[:10]
    if out.get("risk_score") == -1:
        out["risk_score"] = None
    return out


def _csv_response(items: list[dict], filename: str = "export.csv", truncated_msg: str | None = None) -> StreamingResponse:
    """Convert a list of dicts to a CSV streaming response."""
    _no_cache_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }
    if not items:
        return StreamingResponse(
            iter([_CSV_DISCLAIMER]),
            media_type="text/csv",
            headers=_no_cache_headers,
        )
    buf = io.StringIO()
    buf.write(_CSV_DISCLAIMER)
    if truncated_msg:
        buf.write(truncated_msg)
    all_keys: dict[str, None] = {}
    for item in items:
        for k in item:
            all_keys[k] = None
    writer = csv.DictWriter(buf, fieldnames=list(all_keys), lineterminator="\n")
    writer.writeheader()
    writer.writerows(items)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers=_no_cache_headers,
    )



def _wants_csv(request: Request, fmt: str | None) -> bool:
    """Check if the client wants CSV output via ?format=csv or Accept header."""
    if fmt and fmt.lower() == "csv":
        return True
    accept = request.headers.get("accept", "")
    return "text/csv" in accept


@router.get("/facilities", summary="List facilities")
def list_facilities(
    request: Request,
    state: str | None = Query(None, description="Two-letter state code (e.g. TX, CA)"),
    county: str | None = Query(None, description="County name (partial match)"),
    zip: str | None = Query(None, alias="zip", description="5-digit ZIP code"),
    name: str | None = Query(None, description="Facility name (partial match)"),
    program: str | None = Query(None, description="Regulatory program (e.g. CWA, RCRA, CAA)"),
    format: str | None = Query(None, alias="format", description="Response format: json (default) or csv"),
    limit: int = Query(100, ge=1, le=1000, description="Results per page"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    conn=Depends(_get_db),
):
    """List regulated facilities with filtering and pagination.

    Returns facilities from all ingested federal and state sources. Filter by
    state, county, ZIP, name, or regulatory program. Supports CSV export via
    `?format=csv` or `Accept: text/csv` header.

    At least one filter parameter is required to prevent unfiltered queries
    against the full database.
    """
    if not any([state, county, zip, name, program]):
        raise HTTPException(
            status_code=400,
            detail="At least one filter parameter required (state, county, zip, name, or program)",
        )
    clauses = []
    params = []

    if state:
        clauses.append("state = ?")
        params.append(state.upper())
    if county:
        clauses.append("county LIKE ? ESCAPE '\\'")
        params.append(f"%{services.escape_like(county)}%")
    if zip:
        clauses.append("zip_code = ?")
        params.append(zip)
    if name:
        clauses.append("name LIKE ? ESCAPE '\\'")
        params.append(f"%{services.escape_like(name)}%")
    if program:
        clauses.append("programs LIKE ? ESCAPE '\\'")
        params.append(f"%{services.escape_like(program)}%")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    if _wants_csv(request, format):
        # Prefix bare column names with table alias when building JOINed query
        prefixed_where = where.replace("state = ?", "f.state = ?") \
                              .replace("county LIKE ?", "f.county LIKE ?") \
                              .replace("zip_code = ?", "f.zip_code = ?") \
                              .replace("name LIKE ?", "f.name LIKE ?") \
                              .replace("programs LIKE ?", "f.programs LIKE ?")
        csv_cap = 50000
        rows = conn.execute(
            f"SELECT f.*, "
            f"  fm.canonical_id, "
            f"  uf.sources AS unified_sources, "
            f"  uf.source_count, "
            f"  uf.name AS canonical_name "
            f"FROM facilities f "
            f"LEFT JOIN facility_matches fm ON f.source = fm.source AND f.source_id = fm.source_id "
            f"LEFT JOIN unified_facilities uf ON fm.canonical_id = uf.source_id "
            f"{prefixed_where} LIMIT ?",
            params + [csv_cap + 1],
        ).fetchall()
        truncated = len(rows) > csv_cap
        if truncated:
            rows = rows[:csv_cap]
        return _csv_response(
            [_clean_facility_for_csv(r) for r in services.rows_to_dicts(rows)],
            "facilities.csv",
            truncated_msg=f"# Results truncated to {csv_cap:,} rows. Narrow your search with state, county, zip, or name filters.\n" if truncated else None,
        )

    total = conn.execute(f"SELECT COUNT(*) FROM facilities {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM facilities {where} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return _paginated_response(request, services.rows_to_dicts(rows), total, limit, offset)


@router.get("/facilities/{source}/{source_id}", summary="Get facility detail")
def get_facility(
    request: Request,
    source: str = Path(description="Data source name (e.g. epa_echo, tceq, nj_dep)"),
    source_id: str = Path(description="Source-specific facility identifier"),
    conn=Depends(_get_db),
):
    """Retrieve a single facility by its source and source-specific ID.

    The compound key (source, source_id) uniquely identifies a facility across
    all data sources (e.g. epa_echo/FAC001, tceq/RN12345). Includes source
    citation with last ingestion date so users can verify data currency.
    """
    result = services.get_facility_detail(conn, source, source_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Facility not found")
    # Add routes-specific fields
    result["source_citation"]["source_url"] = _source_url(source, source_id, name=result.get("name"))
    if "risk_score" in result:
        result["risk_score"]["methodology_url"] = "/api/v1/methodology"
    return result


@router.get("/facilities/{source}/{source_id}/violations", summary="Get facility violations")
def get_facility_violations(
    request: Request,
    source: str = Path(description="Data source name (e.g. epa_echo, tceq)"),
    source_id: str = Path(description="Source-specific facility identifier"),
    start_date: str | None = Query(None, description="Start date filter (ISO format, e.g. 2024-01-01)"),
    end_date: str | None = Query(None, description="End date filter (ISO format)"),
    since: str | None = Query(None, description="Relative time filter: 2y, 6m, 90d, 1w, or ISO date"),
    program_area: str | None = Query(None, description="Filter by program area (e.g. CWA, RCRA)"),
    limit: int = Query(100, ge=1, le=1000, description="Results per page"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    conn=Depends(_get_db),
):
    """List violations for a specific facility.

    Supports filtering by date range (start_date/end_date or since shorthand)
    and program area. The `since` parameter accepts relative durations like
    `2y` (2 years), `6m` (6 months), `90d`, or `1w`.
    """
    clauses = ["facility_source = ? AND facility_source_id = ?"]
    params: list = [source, source_id]

    effective_start = start_date or (_parse_since(since) if since else None)
    if effective_start:
        clauses.append("violation_date >= ?")
        params.append(effective_start)
    if end_date:
        clauses.append("violation_date <= ?")
        params.append(end_date)
    if program_area:
        clauses.append("program_area = ?")
        params.append(program_area)
    # Exclude clean inspection records (e.g. RCRA "No Violation Identified",
    # CAA "No High Priority Violation") — these are not violations and confuse
    # non-specialists when shown in a Violations table.
    clauses.append(_NOT_CLEAN_STATUS_SQL.format(col="violation_type"))

    where = f"WHERE {' AND '.join(clauses)}"
    total = conn.execute(f"SELECT COUNT(*) FROM violations {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM violations {where} ORDER BY violation_date DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return _paginated_response(request, services.enrich_violations(services.rows_to_dicts(rows)), total, limit, offset)


@router.get("/violations", summary="List violations")
def list_violations(
    request: Request,
    state: str | None = Query(None, description="Two-letter state code"),
    source: str | None = Query(None, description="Data source name (e.g. epa_echo, tceq)"),
    start_date: str | None = Query(None, description="Start date filter (ISO format)"),
    end_date: str | None = Query(None, description="End date filter (ISO format)"),
    since: str | None = Query(None, description="Relative time filter: 2y, 6m, 90d, 1w, or ISO date"),
    program_area: str | None = Query(None, description="Filter by program area (e.g. CWA, RCRA)"),
    limit: int = Query(100, ge=1, le=1000, description="Results per page"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    conn=Depends(_get_db),
):
    """List environmental violations across all sources.

    At least one filter is required: state, source, start_date, since, or program_area.
    Use `since` for convenient relative date filtering (e.g. `since=2y` for violations
    from the last 2 years).
    """
    if not any([state, source, start_date, since, program_area]):
        raise HTTPException(
            status_code=400,
            detail="At least one filter is required: state, source, start_date, since, or program_area.",
        )

    clauses = []
    params = []

    effective_start = start_date or (_parse_since(since) if since else None)

    if effective_start:
        clauses.append("v.violation_date >= ?")
        params.append(effective_start)
    if end_date:
        clauses.append("v.violation_date <= ?")
        params.append(end_date)
    if program_area:
        clauses.append("v.program_area = ?")
        params.append(program_area)
    if source:
        clauses.append("v.facility_source = ?")
        params.append(source)
    # Exclude clean inspection records (e.g. RCRA "No Violation Identified",
    # CAA "No High Priority Violation")
    clauses.append(_NOT_CLEAN_STATUS_SQL.format(col="v.violation_type"))

    if state:
        join_clause = "WHERE v.facility_source_id = f.source_id AND v.facility_source = f.source AND f.state = ?"
        if clauses:
            join_clause += " AND " + " AND ".join(clauses)
        params_full = [state.upper()] + params

        total = conn.execute(
            f"SELECT COUNT(*) FROM violations v, facilities f {join_clause}", params_full
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT v.* FROM violations v, facilities f {join_clause} ORDER BY v.violation_date DESC LIMIT ? OFFSET ?",
            params_full + [limit, offset],
        ).fetchall()
    else:
        # Without state filter, no facility join needed — use v. prefix for consistency
        bare_clauses = [c.replace("v.", "") for c in clauses]
        where = f"WHERE {' AND '.join(bare_clauses)}" if bare_clauses else ""
        total = conn.execute(f"SELECT COUNT(*) FROM violations {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM violations {where} ORDER BY violation_date DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    return _paginated_response(request, services.enrich_violations(services.rows_to_dicts(rows)), total, limit, offset)


@router.get("/unified/facilities", summary="List unified facilities")
def list_unified_facilities(
    request: Request,
    state: str | None = Query(None, description="Two-letter state code"),
    county: str | None = Query(None, description="County name (partial match)"),
    zip: str | None = Query(None, alias="zip", description="5-digit ZIP code"),
    name: str | None = Query(None, description="Facility name (partial match)"),
    limit: int = Query(100, ge=1, le=1000, description="Results per page"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    conn=Depends(_get_db),
):
    """List facilities deduplicated across sources with risk scores.

    The unified view merges facilities that share a source_id across multiple
    data sources, combining their programs, NAICS/SIC codes, and selecting the
    best available name and address. Includes risk scores when available.

    At least one filter parameter is required to prevent unfiltered queries
    against the full database.
    """
    if not any([state, county, zip, name]):
        raise HTTPException(
            status_code=400,
            detail="At least one filter parameter required (state, county, zip, or name)",
        )
    clauses = []
    params = []

    if state:
        clauses.append("u.state = ?")
        params.append(state.upper())
    if county:
        clauses.append("u.county LIKE ? ESCAPE '\\'")
        params.append(f"%{services.escape_like(county)}%")
    if zip:
        clauses.append("u.zip_code = ?")
        params.append(zip)
    if name:
        clauses.append("u.name LIKE ? ESCAPE '\\'")
        params.append(f"%{services.escape_like(name)}%")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM unified_facilities u {where}", params
    ).fetchone()[0]

    try:
        rows = conn.execute(
            f"SELECT u.*, s.score, s.risk_level, s.confidence, "
            f"(SELECT COUNT(*) FROM violations v "
            f"JOIN facility_matches fm ON v.facility_source = fm.source "
            f"AND v.facility_source_id = fm.source_id "
            f"WHERE fm.canonical_id = u.source_id "
            f"AND {_NOT_CLEAN_STATUS_SQL.format(col='v.violation_type')}) AS violation_count "
            f"FROM unified_facilities u LEFT JOIN facility_scores s ON u.source_id = s.source_id "
            f"{where} "
            f"LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    except Exception:
        logger.warning("Unified facilities score join failed, falling back", exc_info=True)
        rows = conn.execute(
            f"SELECT u.* FROM unified_facilities u {where} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    return _paginated_response(request, services.rows_to_dicts(rows), total, limit, offset)


@router.get("/unified/facilities/{source_id:path}/violations", summary="Get unified facility violations")
def get_unified_facility_violations(
    request: Request,
    source_id: str = Path(description="Canonical facility identifier"),
    start_date: str | None = Query(None, description="Start date filter (ISO format)"),
    end_date: str | None = Query(None, description="End date filter (ISO format)"),
    since: str | None = Query(None, description="Relative time filter: 2y, 6m, 90d, 1w, or ISO date"),
    program_area: str | None = Query(None, description="Filter by program area"),
    limit: int = Query(100, ge=1, le=1000, description="Results per page"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    conn=Depends(_get_db),
):
    """List violations for a unified facility.

    Returns all violations linked to the given source_id across all data
    sources. Supports date filtering via start_date/end_date or the `since`
    shorthand (e.g. `since=2y`).
    """
    # Aggregate violations across canonical group via facility_matches
    clauses = ["fm.canonical_id = ?"]
    params: list = [source_id]

    effective_start = start_date or (_parse_since(since) if since else None)
    if effective_start:
        clauses.append("v.violation_date >= ?")
        params.append(effective_start)
    if end_date:
        clauses.append("v.violation_date <= ?")
        params.append(end_date)
    if program_area:
        clauses.append("v.program_area = ?")
        params.append(program_area)
    # Exclude clean inspection records (e.g. RCRA "No Violation Identified",
    # CAA "No High Priority Violation") — these are not violations and confuse
    # non-specialists when shown in a Violations table.
    clauses.append(_NOT_CLEAN_STATUS_SQL.format(col="v.violation_type"))

    where = f"WHERE {' AND '.join(clauses)}"
    base_from = (
        "FROM violations v "
        "JOIN facility_matches fm ON v.facility_source = fm.source "
        "  AND v.facility_source_id = fm.source_id"
    )
    total = conn.execute(
        f"SELECT COUNT(*) {base_from} {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT v.* {base_from} {where} ORDER BY v.violation_date DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return _paginated_response(request, services.enrich_violations(services.rows_to_dicts(rows)), total, limit, offset)


@router.get("/unified/facilities/{source_id:path}", summary="Get unified facility detail")
def get_unified_facility(
    request: Request,
    source_id: str = Path(description="Canonical facility identifier (may be an EPA RegistryID for matched facilities)"),
    conn=Depends(_get_db),
):
    """Retrieve a single unified facility by source_id.

    Returns the deduplicated facility record with merged data from all sources
    that share this source_id. Includes risk score with confidence level and
    source citations for each contributing data source.
    """
    row = conn.execute(
        "SELECT * FROM unified_facilities WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    if not row:
        # Fallback: unified_facilities may be stale (e.g. entity resolution ran
        # but rebuild_unified_table hasn't yet).  Build a minimal response from
        # facility_matches + facilities so the drawer never returns 404 for a
        # valid canonical_id.
        fm_rows = conn.execute(
            "SELECT fm.source, fm.source_id, f.name, f.address, f.city, f.state, "
            "f.zip_code, f.county, f.lat, f.lon, f.naics_codes, f.sic_codes, "
            "f.programs, f.last_updated "
            "FROM facility_matches fm "
            "JOIN facilities f ON fm.source = f.source AND fm.source_id = f.source_id "
            "WHERE fm.canonical_id = ?",
            (source_id,),
        ).fetchall()
        if not fm_rows:
            raise HTTPException(status_code=404, detail="Facility not found")
        # Merge fields: prefer non-null/non-empty values
        result: dict = {"source_id": source_id}
        for field in ("name", "address", "city", "state", "zip_code", "county", "lat", "lon",
                      "naics_codes", "sic_codes", "programs", "last_updated"):
            for r in fm_rows:
                v = r[field]
                if v is not None and v != "" and v != "Unknown":
                    result[field] = v
                    break
            else:
                result.setdefault(field, None)
        result["sources"] = ",".join(sorted({r["source"] for r in fm_rows}))
        result["source_count"] = len({r["source"] for r in fm_rows})
        source_list = [s.strip() for s in result["sources"].split(",") if s.strip()]
    else:
        result = dict(row)
        source_list = [s.strip() for s in (row["sources"] or "").split(",") if s.strip()]

    # Score with confidence
    score_row = conn.execute(
        "SELECT score, risk_level, confidence, violation_count, "
        "raw_violation_count, latest_violation_date, naics_tier, program_count "
        "FROM facility_scores WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    # Live violation count and latest violation date — always computed from violations
    # table so they're accurate even when facility_scores is stale or missing.
    # Exclude clean inspection records (e.g. RCRA "No Violation Identified",
    # CAA "No High Priority Violation").
    live_count = conn.execute(
        "SELECT COUNT(*) FROM violations v "
        "JOIN facility_matches fm ON v.facility_source = fm.source "
        "AND v.facility_source_id = fm.source_id "
        "WHERE fm.canonical_id = ? "
        f"AND {_NOT_CLEAN_STATUS_SQL.format(col='v.violation_type')}",
        (source_id,),
    ).fetchone()[0]
    live_latest = conn.execute(
        "SELECT MAX(v.violation_date) FROM violations v "
        "JOIN facility_matches fm ON v.facility_source = fm.source "
        "AND v.facility_source_id = fm.source_id "
        "WHERE fm.canonical_id = ? "
        f"AND {_NOT_CLEAN_STATUS_SQL.format(col='v.violation_type')}",
        (source_id,),
    ).fetchone()[0]
    live_latest = services._filter_sentinel_date(live_latest)
    if score_row:
        result["risk_score"] = dict(score_row)
        # Filter sentinel dates (e.g. 3000-12-31) from cached scoring results
        result["risk_score"]["latest_violation_date"] = services._filter_sentinel_date(
            result["risk_score"].get("latest_violation_date")
        ) or live_latest
        result["risk_score"]["violation_count"] = live_count
        result["risk_score"]["methodology_url"] = "/api/v1/methodology"
    else:
        # No score entry yet (e.g. unified_facilities not rebuilt after ingest).
        # Provide a minimal risk_score dict with live violation data so the
        # drawer can still show Last Violation date and violation count.
        if live_count > 0:
            result["risk_score"] = {
                "score": -1,
                "risk_level": "unscored",
                "confidence": "low",
                "violation_count": live_count,
                "raw_violation_count": live_count,
                "latest_violation_date": live_latest,
                "naics_tier": None,
                "program_count": None,
                "methodology_url": "/api/v1/methodology",
            }

    # Source citations — one per contributing source
    citations = []
    if source_list:
        placeholders = ",".join("?" for _ in source_list)
        ingest_rows = conn.execute(
            f"SELECT source, MAX(completed_at) AS last_ingest FROM pipeline_ops "
            f"WHERE source IN ({placeholders}) AND status = 'completed' GROUP BY source",
            source_list,
        ).fetchall()
        ingest_map = {r["source"]: r["last_ingest"] for r in ingest_rows}
        # Fetch per-source source_ids so the URL uses the correct native ID
        # (e.g. TCEQ RN, not the canonical/EPA FRS ID)
        src_id_rows = conn.execute(
            f"SELECT source, source_id FROM facility_matches "
            f"WHERE canonical_id = ? AND source IN ({placeholders})",
            [source_id] + source_list,
        ).fetchall()
        src_id_map = {r["source"]: r["source_id"] for r in src_id_rows}
        facility_name = result.get("name")
        for src in source_list:
            native_id = src_id_map.get(src, source_id)
            citations.append({
                "source": src,
                "source_url": _source_url(src, native_id, name=facility_name),
                "last_ingested": ingest_map.get(src),
            })
    result["source_citations"] = citations

    return result


_SEARCH_ORDER_BY_FIELDS = {"distance", "violation_count", "risk_score", "name"}

# Sources that are inherently PFAS-related.  Used by the PFAS program filter
# to match facilities by source in addition to the programs field.
PFAS_SOURCES = frozenset({
    "epa_pfas", "epa_ucmr", "mi_pfas", "nj_pfas", "wi_pfas",
    "oh_pfas", "il_pfas",
})


def _match_pfas(r: dict) -> bool:
    """Return True if a facility is PFAS-associated (source or program)."""
    src = (r.get("source") or "").lower()
    if src in PFAS_SOURCES:
        return True
    unified = (r.get("unified_sources") or "").lower()
    if any(s.strip() in PFAS_SOURCES for s in unified.split(",")):
        return True
    programs = (r.get("programs") or "").lower()
    if "pfas" in programs:
        return True
    # FL DEP brownfields PFAS subset: source_id starts with "pfas-"
    if src == "fl_dep_bf" and (r.get("source_id") or "").startswith("pfas-"):
        return True
    return False


@router.get("/search", summary="Radius search")
def search_facilities(
    request: Request,
    address: str | None = Query(None, description="US street address to geocode (mutually exclusive with lat/lon)"),
    lat: float | None = Query(None, description="Center latitude (decimal degrees)"),
    lon: float | None = Query(None, description="Center longitude (decimal degrees)"),
    radius: float = Query(1.0, ge=0.1, le=50.0, description="Search radius in miles (default: 1)"),
    name: str | None = Query(None, description="Filter by facility name (case-insensitive substring match)"),
    program: str | None = Query(None, description="Filter by regulatory program (e.g. RCRA, CWA, CAA, SDWA, Superfund, LUST/UST, PFAS, State)"),
    pfas_only: bool = Query(False, description="When true, only return PFAS-associated facilities (equivalent to program=PFAS)"),
    format: str | None = Query(None, alias="format", description="Response format: json (default) or csv"),
    limit: int = Query(100, ge=1, le=1000, description="Results per page"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    order_by: str | None = Query(None, description="Sort field: distance (default), violation_count, risk_score, name"),
    order_dir: str | None = Query(None, description="Sort direction: asc or desc (default depends on field)"),
    min_score: int | None = Query(None, ge=0, le=100, description="Exclude facilities with risk_score below this value (e.g. min_score=1 hides score-0 registry-only entries)"),
    conn=Depends(_get_db),
):
    """Find all regulated facilities within a radius of a point.

    Provide either a US street address (geocoded via Census Bureau API) or
    lat/lon coordinates. Returns facilities sorted by distance with violation
    counts. Useful for environmental screening of a location. Supports
    CSV export via `?format=csv`.
    """
    if address and (lat is not None or lon is not None):
        raise HTTPException(status_code=400, detail="Provide either address or lat/lon, not both")

    if order_by is not None and order_by not in _SEARCH_ORDER_BY_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"order_by must be one of: {', '.join(sorted(_SEARCH_ORDER_BY_FIELDS))}",
        )
    if order_dir is not None and order_dir not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order_dir must be 'asc' or 'desc'")

    matched_address = None
    if address:
        coords = _geocode_address(address)
        if not coords:
            raise HTTPException(status_code=422, detail=_geocode_error_detail(address))
        center_lat, center_lon, matched_address = coords
    elif lat is not None and lon is not None:
        center_lat, center_lon = lat, lon
    else:
        raise HTTPException(status_code=400, detail="Provide address or lat/lon")

    # Extract center state for state_mismatch flagging
    center_state = None
    if matched_address:
        from ..geo import _extract_state
        center_state = _extract_state(matched_address)
    if center_state is None:
        from ..geo import reverse_geocode_state
        center_state = reverse_geocode_state(center_lat, center_lon)
    results = services.search_radius(
        conn, center_lat, center_lon, radius, center_state=center_state,
    )

    # Name filter (case-insensitive substring, routes-specific)
    if name:
        name_lower = name.lower()
        results = [r for r in results if name_lower in (r.get("name") or "").lower()]

    # pfas_only shorthand: treat as program=PFAS
    if pfas_only and not program:
        program = "PFAS"

    # Program filter — mirrors client-side matchesProgram() logic for Superfund and LUST/UST aliases
    if program and program != "all":
        prog_lower = program.lower()
        if prog_lower == "superfund":
            def _match_superfund(r):
                p = (r.get("programs") or "").lower()
                return "superfund" in p or "sems" in p or "npl" in p or "cerclis" in p
            results = [r for r in results if _match_superfund(r)]
        elif prog_lower in ("lust/ust", "lust", "ust"):
            def _match_lust(r):
                p = (r.get("programs") or "").lower()
                return (
                    bool(re.search(r"\blust\b", p))
                    or bool(re.search(r"\bust\b", p))
                    or "underground storage tank" in p
                    or "leaking underground storage" in p
                    or bool(re.search(r"\bpstreg\b", p))
                    or bool(re.search(r"\blpstrmd\b", p))
                    or bool(re.search(r"\bstageii\b", p))
                )
            results = [r for r in results if _match_lust(r)]
        elif prog_lower == "state":
            # State filter: match facilities from any state-level source (non-EPA).
            # Checks both the primary source and unified_sources (comma-separated).
            def _match_state_source(r):
                src = (r.get("source") or "").lower()
                unified = (r.get("unified_sources") or "").lower()
                if src and not src.startswith("epa_"):
                    return True
                return any(
                    s.strip() and not s.strip().startswith("epa_")
                    for s in unified.split(",")
                )
            results = [r for r in results if _match_state_source(r)]
        elif prog_lower == "rcra ca":
            # RCRA Corrective Action: facilities with active mandated cleanup orders.
            # These are stored as programs="RCRA CA" (distinct from plain "RCRA").
            results = [r for r in results if "RCRA CA" in (r.get("programs") or "")]
        elif prog_lower == "rcra":
            # RCRA generators/handlers: match RCRA and TCEQ equivalents,
            # but exclude RCRA CA (corrective action) facilities — those have
            # their own dedicated filter chip.
            def _match_rcra(r):
                p = (r.get("programs") or "").upper()
                return (
                    bool(re.search(r"\bRCRA\b", p))
                    and "RCRA CA" not in p
                ) or bool(re.search(r"\bIHW\b", p)) or bool(re.search(r"\bIHWCA\b", p)) or bool(re.search(r"\bHW\b", p))
            results = [r for r in results if _match_rcra(r)]
        elif prog_lower == "sdwa":
            # SDWA (Safe Drinking Water Act): water systems serve geographic areas
            # (cities/counties) but their stored coordinates are at the treatment
            # plant, which may be miles outside the search radius.  First keep any
            # SDWA-tagged facilities already in the radius results (e.g. industrial
            # facilities that appear in both ECHO and SDWA).  Then augment with a
            # county-level lookup that returns all community water systems serving
            # the county at the search center, regardless of where the plant is.
            # This ensures "Newark Water Department" (plant 12 mi away) shows up
            # when searching for any address in Essex County, NJ.
            in_radius_sdwa = [r for r in results if "SDWA" in (r.get("programs") or "").upper()]
            in_radius_ids = {r.get("canonical_id") or r.get("source_id") for r in in_radius_sdwa}
            sdwa_county_results: list[dict] = []
            if center_state:
                _, search_county = _reverse_geocode_city_county(center_lat, center_lon)
                if search_county:
                    sdwa_county_results = services.search_sdwa_by_county(
                        conn, center_state, search_county, center_lat, center_lon
                    )
            # Merge: radius hits first, then county-level additions not already included
            extra = [
                r for r in sdwa_county_results
                if (r.get("canonical_id") or r.get("source_id")) not in in_radius_ids
            ]
            results = in_radius_sdwa + extra
        elif prog_lower == "pfas":
            results = [r for r in results if _match_pfas(r)]
        else:
            results = [r for r in results if program in (r.get("programs") or "")]

    # min_score filter: exclude facilities whose risk_score is below the threshold.
    # risk_score == -1 means unscored (no score record); those are kept to avoid
    # hiding facilities that simply haven't been scored yet.
    if min_score is not None:
        results = [
            r for r in results
            if r.get("risk_score") is None
            or r.get("risk_score") == -1
            or r.get("risk_score") >= min_score
        ]

    # Enrich with live violation counts from the violations table via facility_matches.
    # facility_scores.violation_count (used by search_radius) may be stale when cross-source
    # matches were created after the last score run, causing violations from matched sources
    # (e.g. SDWIS violations on a CAA facility) to be missed (CIV-635).
    services.enrich_search_violation_counts(conn, results)

    # Server-side sort across all results before pagination.
    # Default sort is by distance (already applied in search_radius).
    if order_by and order_by != "distance":
        reverse = order_dir == "desc" if order_dir else order_by in ("violation_count", "risk_score")
        if order_by == "violation_count":
            results.sort(key=lambda r: r.get("violation_count") or 0, reverse=reverse)
        elif order_by == "risk_score":
            # Higher score = higher risk; sort descending by default so highest risk first
            results.sort(key=lambda r: r.get("risk_score") if r.get("risk_score") is not None else -1, reverse=reverse)
        elif order_by == "name":
            results.sort(key=lambda r: (r.get("name") or "").lower(), reverse=reverse)
    elif order_by == "distance":
        reverse = order_dir == "desc"
        results.sort(key=lambda r: r.get("distance_miles") or 0, reverse=reverse)

    total = len(results)
    page = results[offset : offset + limit]

    if _wants_csv(request, format):
        return _csv_response(
            [_clean_facility_for_csv(r) for r in results],
            "search_results.csv",
        )

    # Aggregate stats across ALL results (not just the current page)
    total_high = sum(1 for r in results if r.get("risk_level") in ("high", "critical"))
    total_medium = sum(1 for r in results if r.get("risk_level") == "medium")
    total_violations = sum(r.get("violation_count") or 0 for r in results)
    facilities_with_violations = sum(1 for r in results if (r.get("violation_count") or 0) > 0)

    center_info = {"lat": center_lat, "lon": center_lon}
    if matched_address:
        center_info["matched_address"] = matched_address
        if address:
            # Approximate fallback: geocoder returned either a street centerline
            # or a city/zip centroid because the full address was not found.
            if "(approximate — matched street" in matched_address:
                center_info["geocode_quality"] = "street_approx"
                center_info["geocode_warning"] = (
                    "Exact address not found in geocoding databases. "
                    "Showing results centered on the nearest matching street. "
                    "Results within ~0.25 miles may be slightly off. "
                    "Try entering GPS coordinates for a precise location."
                )
            elif "(approximate" in matched_address:
                center_info["geocode_quality"] = "zip_centroid"
                center_info["geocode_warning"] = (
                    "Address not found in public geocoding databases — "
                    "results are centered on the zip code area, which may be "
                    "miles from the actual property. "
                    "Do not use this report without verifying the search location. "
                    "Enter GPS coordinates (e.g. from Google Maps) for an accurate search."
                )
            else:
                center_info["geocode_quality"] = "exact"
                geocode_warning = _detect_road_type_substitution(
                    address, matched_address, lat=center_lat, lon=center_lon
                )
                if geocode_warning:
                    center_info["geocode_warning"] = geocode_warning
    return _paginated_response(
        request, page, total, limit, offset,
        center=center_info,
        radius_miles=radius,
        total_high=total_high,
        total_medium=total_medium,
        total_violations=total_violations,
        facilities_with_violations=facilities_with_violations,
    )


@router.get("/search/violations", summary="Bulk violation export for a radius")
def search_violations(
    request: Request,
    address: str | None = Query(None, description="US street address to geocode"),
    lat: float | None = Query(None, description="Center latitude"),
    lon: float | None = Query(None, description="Center longitude"),
    radius: float = Query(1.0, ge=0.1, le=50.0, description="Search radius in miles"),
    since: str | None = Query(None, description="Only violations after this date (ISO or relative: 2y, 6m, 90d)"),
    program: str | None = Query(None, description="Filter by program area (e.g. RCRA, CWA, CAA, SDWA, Air, Water)"),
    severity: str | None = Query(None, description="Filter by severity (e.g. High, Medium, Low, Significant)"),
    format: str | None = Query(None, alias="format", description="Response format: json (default) or csv"),
    limit: int = Query(5000, ge=1, le=10000, description="Max violations to return"),
    conn=Depends(_get_db),
):
    """Return all violations for facilities within a radius.

    Returns a flat list of violations with facility name and distance,
    suitable for CSV export. Use `?format=csv` for spreadsheet download.
    """
    if address and (lat is not None or lon is not None):
        raise HTTPException(status_code=400, detail="Provide either address or lat/lon, not both")

    matched_address = None
    if address:
        coords = _geocode_address(address)
        if not coords:
            raise HTTPException(status_code=422, detail=_geocode_error_detail(address))
        center_lat, center_lon, matched_address = coords
    elif lat is not None and lon is not None:
        center_lat, center_lon = lat, lon
    else:
        raise HTTPException(status_code=400, detail="Provide address or lat/lon")

    since_date = _parse_since(since) if since else None

    # Extract center state for state_mismatch filtering
    center_state = None
    if matched_address:
        from ..geo import _extract_state
        center_state = _extract_state(matched_address)
    if center_state is None:
        from ..geo import reverse_geocode_state
        center_state = reverse_geocode_state(center_lat, center_lon)

    # Use shared radius search for facility lookup
    fac_results = services.search_radius(conn, center_lat, center_lon, radius, center_state=center_state)
    cid_info: dict[str, dict] = {}
    for fac in fac_results:
        cid = fac.get("canonical_id") or fac["source_id"]
        cid_info[cid] = {
            "distance": fac["distance_miles"],
            "name": fac.get("name"),
            "address": fac.get("address"),
            "city": fac.get("city"),
            "state": fac.get("state"),
        }

    if not cid_info:
        if _wants_csv(request, format):
            return _csv_response([], "violations.csv")
        return {"violations": [], "total": 0}

    # Batch-fetch violations for all canonical_ids
    extra_clauses = []
    extra_params: list = []
    if since_date:
        extra_clauses.append("v.violation_date >= ?")
        extra_params.append(since_date)
    if program:
        extra_clauses.append("v.program_area LIKE ? ESCAPE '\\'")
        extra_params.append(f"%{services.escape_like(program)}%")
    if severity:
        extra_clauses.append("v.severity = ?")
        extra_params.append(severity)
    extra_sql = (" AND " + " AND ".join(extra_clauses)) if extra_clauses else ""

    cid_list = list(cid_info.keys())
    all_viols = []
    batch_size = 500
    for i in range(0, len(cid_list), batch_size):
        batch = cid_list[i:i + batch_size]
        placeholders = ",".join("?" * len(batch))
        params = list(batch) + extra_params
        vrows = conn.execute(
            f"SELECT fm.canonical_id, v.violation_type, v.violation_date, "
            f"  v.severity, v.program_area, v.statute, v.description "
            f"FROM violations v "
            f"JOIN facility_matches fm ON v.facility_source = fm.source AND v.facility_source_id = fm.source_id "
            f"WHERE fm.canonical_id IN ({placeholders}) "
            f"AND {_NOT_CLEAN_STATUS_SQL.format(col='v.violation_type')}{extra_sql} "
            f"ORDER BY v.violation_date DESC",
            params,
        ).fetchall()
        for vrow in vrows:
            vdict = dict(vrow)
            cid = vdict.pop("canonical_id")
            info = cid_info[cid]
            vdict["facility_name"] = info["name"]
            vdict["facility_address"] = info["address"]
            vdict["facility_city"] = info["city"]
            vdict["facility_state"] = info["state"]
            vdict["distance_miles"] = round(info["distance"], 3)
            services.enrich_violation_dict(vdict)
            all_viols.append(vdict)

    all_viols.sort(key=lambda x: (x["distance_miles"], x.get("violation_date") or ""))
    total = len(all_viols)
    page = all_viols[:limit]

    if _wants_csv(request, format):
        return _csv_response(page, "violations.csv")
    return {"violations": page, "total": total}


def _screening_report_csv_rows(report: dict) -> list[dict]:
    """Flatten a screening report's facilities into a list of dicts for CSV export."""
    rows = []
    site = report.get("site", {})
    for fac in report.get("facilities", []):
        row = {
            "site_address": site.get("input_address") or site.get("address") or "",
            "site_lat": site.get("lat", ""),
            "site_lon": site.get("lon", ""),
            "search_radius_miles": site.get("search_radius_miles", ""),
            "name": fac.get("name") or "",
            "address": fac.get("address") or "",
            "city": fac.get("city") or "",
            "state": fac.get("state") or "",
            "zip_code": fac.get("zip_code") or "",
            "source": fac.get("source") or "",
            "source_id": fac.get("source_id") or "",
            "unified_sources": fac.get("unified_sources") or fac.get("source") or "",
            "source_count": fac.get("source_count") or 1,
            "programs": _humanize_programs(fac.get("programs") or fac.get("unified_programs") or "", state=fac.get("state")),
            "distance_miles": fac.get("distance_miles", ""),
            "risk_score": fac.get("risk_score") if fac.get("risk_score", -1) != -1 else "",
            "risk_level": fac.get("risk_level") or "",
            "confidence": fac.get("confidence") or "",
            "violation_count": fac.get("violation_count", 0),
            "active_violation_count": fac.get("active_violation_count", 0),
            "latest_violation_date": fac.get("latest_violation_date") or "",
        }
        rows.append(row)
    return rows


_PDF_SORT_FIELDS = {"risk", "distance", "violations", "name"}

# Limit concurrent in-memory PDF builds to prevent OOM under load.
_PDF_SEMAPHORE = asyncio.Semaphore(3)

# Hard cap on search radius to limit query size (PDF requests especially).
_MAX_RADIUS_MILES = 5.0


@router.get("/reports/screening", summary="Environmental screening report")
async def screening_report(
    request: Request,
    address: str | None = Query(None, description="Site address to geocode, or display hint when lat/lon are provided"),
    lat: float | None = Query(None, description="Site latitude"),
    lon: float | None = Query(None, description="Site longitude"),
    radius: float = Query(1.0, ge=0.1, le=5.0, description="Search radius in miles (max 5)"),
    format: str | None = Query(None, description="Response format: json (default), pdf, or csv"),
    sort_by: str | None = Query(None, description="PDF sort order: risk (default), distance, violations, name"),
    conn=Depends(_get_db),
):
    """Generate an environmental screening report from public government records.

    Takes a site address (or lat/lon) and returns all facilities within the
    search radius, their violations, risk scores, and source citations.
    This is not a professional environmental site assessment.
    """
    input_address: str | None = None
    geocode_quality: str = "exact"
    geocode_warning: str | None = None
    if lat is not None and lon is not None:
        center_lat, center_lon = lat, lon
        # address is accepted as a display hint alongside lat/lon (not geocoded).
        # Treat it as input_address so the PDF "Site:" line shows what the caller
        # provided (the user's original search string) rather than a geocoded result.
        input_address = address or None
        resolved_address = address or None
    elif address:
        coords = _geocode_address(address)
        if not coords:
            raise HTTPException(status_code=422, detail=_geocode_error_detail(address))
        center_lat, center_lon, matched_address = coords
        input_address = address
        resolved_address = matched_address
        if "(approximate — matched street" in matched_address:
            geocode_quality = "street_approx"
        elif "(approximate" in matched_address:
            geocode_quality = "zip_centroid"
        else:
            road_warn = _detect_road_type_substitution(
                address, matched_address, lat=center_lat, lon=center_lon
            )
            if road_warn:
                geocode_quality = "address_mismatch"
                geocode_warning = road_warn
    else:
        raise HTTPException(status_code=400, detail="Provide address or lat/lon")

    report = services.build_screening_report(
        conn, center_lat, center_lon, radius, resolved_address, input_address,
    )
    report["methodology_url"] = "/api/v1/methodology"
    report["coverage_url"] = "/api/v1/coverage"
    # Expose geocode quality in the report's site section so PDF can show appropriate warning.
    report["site"]["geocode_quality"] = geocode_quality
    if geocode_warning:
        report["site"]["geocode_warning"] = geocode_warning

    if _wants_csv(request, format):
        rows = _screening_report_csv_rows(report)
        return _csv_response(rows, "screening_report.csv")

    if format == "pdf":
        # Reject immediately if all PDF slots are taken (DoS protection).
        if _PDF_SEMAPHORE._value <= 0:  # noqa: SLF001
            raise HTTPException(
                status_code=429,
                detail="Server busy generating PDFs. Please retry in a moment.",
                headers={"Retry-After": "5"},
            )
        pdf_sort = sort_by if sort_by in _PDF_SORT_FIELDS else "risk"
        loop = asyncio.get_event_loop()
        async with _PDF_SEMAPHORE:
            try:
                pdf_bytes = await loop.run_in_executor(
                    None, lambda: _generate_screening_pdf(report, sort_by=pdf_sort)
                )
            except Exception:
                logger.exception("PDF generation failed for screening report")
                raise HTTPException(
                    status_code=422,
                    detail="PDF generation failed. Try CSV format instead.",
                )
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="screening-report.pdf"',
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    return report


def _compute_stats() -> dict:
    """Run full-table stats queries and return the result dict.

    Opens its own DB connection so it can be called from a background thread
    (the request-scoped connection must not be shared across threads).
    """
    conn = get_connection()
    try:
        fac = conn.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]
        vio = conn.execute("SELECT COUNT(*) FROM violations").fetchone()[0]
        sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM facilities").fetchall()]
        states = [r[0] for r in conn.execute(
            "SELECT DISTINCT state FROM facilities WHERE state IS NOT NULL"
        ).fetchall()]
        last = conn.execute(
            "SELECT completed_at FROM pipeline_ops WHERE status='completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        unified_fac = conn.execute("SELECT COUNT(*) FROM unified_facilities").fetchone()[0]
        multi_source = conn.execute(
            "SELECT COUNT(*) FROM unified_facilities WHERE source_count > 1"
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "total_facilities": fac,
        "total_violations": vio,
        "sources": sources,
        "states": states,
        "last_ingestion": last[0] if last else None,
        "unified_facility_count": unified_fac,
        "multi_source_facility_count": multi_source,
    }


@router.get("/stats", summary="Database statistics")
def get_stats(request: Request):
    """Aggregate statistics for the entire database.

    Returns total facility and violation counts, list of ingested sources and
    states, last ingestion timestamp, and unified facility counts.

    Response is cached for 1 hour (TTL_SECONDS=3600) to avoid expensive
    full-table COUNT queries on every request against the 6M+ facility database.
    Stale values (within 24 h of the last refresh) are served immediately
    while a background thread refreshes the cache, so no request ever blocks
    on a cold COUNT(*) scan after startup.
    """
    cache = get_cache()
    cache_key = "stats"
    cached = cache.get(cache_key, refresh_fn=_compute_stats)
    if cached is not None:
        return cached
    # True cold start (no value at all) — must compute synchronously.
    result = _compute_stats()
    cache.set(cache_key, result)
    return result


@router.get("/sources", summary="List data sources")
def list_sources(request: Request, conn=Depends(_get_db)):
    """List all ingested data sources with record counts and freshness.

    Returns each source's facility count, violation count, and the timestamp
    of its most recent successful ingestion. Sources are sorted by facility
    count descending.
    """

    rows = conn.execute(
        """
        SELECT
            f.source,
            COUNT(*) AS facility_count,
            COALESCE(v.violation_count, 0) AS violation_count,
            il.last_ingest
        FROM facilities f
        LEFT JOIN (
            SELECT facility_source AS source, COUNT(*) AS violation_count
            FROM violations
            GROUP BY facility_source
        ) v ON f.source = v.source
        LEFT JOIN (
            SELECT source, MAX(completed_at) AS last_ingest
            FROM pipeline_ops
            WHERE status = 'completed'
            GROUP BY source
        ) il ON f.source = il.source
        GROUP BY f.source
        ORDER BY COUNT(*) DESC
        """
    ).fetchall()

    return {
        "sources": [
            {
                "name": r["source"],
                "facility_count": r["facility_count"],
                "violation_count": r["violation_count"],
                "last_ingest": r["last_ingest"],
            }
            for r in rows
        ],
        "total": len(rows),
    }


def _compute_coverage(state: str | None = None) -> dict:
    """Run coverage matrix queries and return the result dict.

    Opens its own DB connection so it can be called from a background thread
    (the request-scoped connection must not be shared across threads).
    """
    conn = get_connection()
    try:
        state_upper = state.upper() if state else None

        clauses = []
        params = []
        if state_upper:
            clauses.append("f.state = ?")
            params.append(state_upper)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        # Per-state, per-source facility counts
        fac_rows = conn.execute(
            f"SELECT f.state, f.source, COUNT(*) AS facility_count "
            f"FROM facilities f {where} "
            f"GROUP BY f.state, f.source ORDER BY f.state, f.source",
            params,
        ).fetchall()

        # Per-source violation counts (by state via facility join)
        vio_clauses = list(clauses)
        vio_params = list(params)
        vio_where = f"WHERE {' AND '.join(vio_clauses)}" if vio_clauses else ""

        vio_rows = conn.execute(
            f"SELECT f.state, v.facility_source AS source, COUNT(*) AS violation_count "
            f"FROM violations v "
            f"JOIN facilities f ON v.facility_source = f.source AND v.facility_source_id = f.source_id "
            f"{vio_where} "
            f"GROUP BY f.state, v.facility_source",
            vio_params,
        ).fetchall()

        # Last ingestion per source
        ingest_rows = conn.execute(
            "SELECT source, MAX(completed_at) AS last_ingest "
            "FROM pipeline_ops WHERE status = 'completed' GROUP BY source"
        ).fetchall()

        # Score distribution per state
        score_clauses = []
        score_params = []
        if state_upper:
            score_clauses.append("u.state = ?")
            score_params.append(state_upper)

        score_where = f"WHERE {' AND '.join(score_clauses)}" if score_clauses else ""

        score_rows = conn.execute(
            f"SELECT u.state, s.risk_level, COUNT(*) AS cnt "
            f"FROM facility_scores s "
            f"JOIN unified_facilities u ON s.source_id = u.source_id "
            f"{score_where} "
            f"GROUP BY u.state, s.risk_level",
            score_params,
        ).fetchall()
    finally:
        conn.close()

    # Build lookup dicts
    vio_lookup = {}
    for r in vio_rows:
        vio_lookup[(r["state"], r["source"])] = r["violation_count"]

    ingest_lookup = {r["source"]: r["last_ingest"] for r in ingest_rows}

    score_lookup: dict[str, dict] = {}
    for r in score_rows:
        st = r["state"]
        if st not in score_lookup:
            score_lookup[st] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        if r["risk_level"] in score_lookup[st]:
            score_lookup[st][r["risk_level"]] = r["cnt"]

    # Build state-level coverage entries
    states_data: dict[str, dict] = {}
    for r in fac_rows:
        st = r["state"]
        if st not in states_data:
            states_data[st] = {"state": st, "sources": [], "total_facilities": 0, "total_violations": 0}
        vc = vio_lookup.get((st, r["source"]), 0)
        states_data[st]["sources"].append({
            "source": r["source"],
            "facility_count": r["facility_count"],
            "violation_count": vc,
            "last_ingest": ingest_lookup.get(r["source"]),
        })
        states_data[st]["total_facilities"] += r["facility_count"]
        states_data[st]["total_violations"] += vc

    for st, data in states_data.items():
        data["score_distribution"] = score_lookup.get(st, {"critical": 0, "high": 0, "medium": 0, "low": 0})
        sources_with_violations = sum(1 for s in data["sources"] if s["violation_count"] > 0)
        data["sources_with_violations"] = sources_with_violations
        data["total_sources"] = len(data["sources"])

    sorted_states = sorted(states_data.values(), key=lambda x: x["state"])

    # Summary totals
    total_fac = sum(s["total_facilities"] for s in sorted_states)
    total_vio = sum(s["total_violations"] for s in sorted_states)
    states_with_violations = sum(1 for s in sorted_states if s["total_violations"] > 0)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_states": len(sorted_states),
            "total_facilities": total_fac,
            "total_violations": total_vio,
            "states_with_violation_data": states_with_violations,
        },
        "states": sorted_states,
        "methodology_url": "/api/v1/methodology",
    }


@router.get("/coverage", summary="Data coverage matrix")
def coverage_matrix(
    request: Request,
    state: str | None = Query(None, description="Filter to a single state"),
):
    """Per-state, per-source data coverage matrix.

    Shows facility count, violation count, and last ingestion date for each
    source in each state. This is the transparency endpoint: it tells users
    exactly what data exists (and what doesn't) behind every compliance score.

    Use this to understand score confidence — a state with 0 violations
    likely means data is missing, not that facilities are clean.

    Response is cached for 1 hour to avoid expensive join queries across
    the 6M+ facility database on every request. Stale values (within 24 h)
    are served immediately while the cache refreshes in the background.
    """
    cache = get_cache()
    cache_key = f"coverage:{(state or '').upper()}"
    refresh_fn = lambda: _compute_coverage(state)  # noqa: E731
    cached = cache.get(cache_key, refresh_fn=refresh_fn)
    if cached is not None:
        return cached
    # True cold start (no value at all) — must compute synchronously.
    result = _compute_coverage(state)
    cache.set(cache_key, result)
    return result


@router.get("/methodology", summary="Scoring methodology")
def get_methodology(request: Request):
    """Returns the compliance risk scoring methodology.

    Documents exactly how the Compliance Risk Score (CRS) is computed:
    the five scoring layers, deduction values, risk level thresholds, data
    sources, and known limitations. This is the trust document — it lets users
    verify that any score is reproducible from public government data.
    """
    from pathlib import Path

    doc_path = Path(__file__).parent.parent.parent / "docs" / "scoring-methodology.md"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail="Methodology document not found")

    content = doc_path.read_text()
    return {
        "format": "markdown",
        "version": "1.0",
        "content": content,
    }


@router.get("/terms", summary="Terms of Service", include_in_schema=False)
def terms_page():
    """Serve the Terms of Service HTML page."""
    from pathlib import Path as P
    terms_path = P(__file__).parent / "static" / "terms.html"
    if not terms_path.exists():
        raise HTTPException(status_code=404, detail="Terms page not found")
    return FileResponse(terms_path, media_type="text/html")


@router.get("/health", summary="Health check")
def health_check(request: Request, conn=Depends(_get_db)):
    """System health and data freshness overview.

    Returns database connectivity status, last ingestion time, and per-source
    freshness (how many hours since each source was last ingested). Useful
    for monitoring and alerting on stale data.

    Counts are served from the stats cache when available to avoid full table
    scans on the 5M+ row facilities table. Use /api/v1/stats for authoritative
    record counts (cached separately, 5-minute TTL).
    """
    now = datetime.now(timezone.utc)

    # Lightweight connectivity check — avoids full table scan on 5M+ rows.
    # COUNT(*) on a large un-indexed table takes seconds and can cascade into
    # Docker health-check failures and OOM-kill restart loops.
    conn.execute("SELECT 1").fetchone()

    # Use cached counts if available; fall back to None rather than a slow COUNT.
    cache = get_cache()
    cached_stats = cache.get("stats")
    if cached_stats is not None:
        fac = cached_stats.get("total_facilities")
        vio = cached_stats.get("total_violations")
    else:
        fac = None
        vio = None

    last = conn.execute(
        "SELECT completed_at FROM pipeline_ops WHERE status='completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()

    # Per-source freshness — LEFT JOIN from all known sources (facilities table)
    # so that sources without pipeline_ops entries still appear (last_ingest=null).
    source_rows = conn.execute(
        """
        SELECT f.source, il.last_ingest
        FROM (SELECT DISTINCT source FROM facilities) f
        LEFT JOIN (
            SELECT source, MAX(completed_at) AS last_ingest
            FROM pipeline_ops WHERE status = 'completed' GROUP BY source
        ) il ON f.source = il.source
        """
    ).fetchall()

    source_freshness = []
    for r in source_rows:
        last_dt = r["last_ingest"]
        hours_ago = None
        if last_dt:
            try:
                parsed = datetime.fromisoformat(last_dt.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                hours_ago = round((now - parsed).total_seconds() / 3600, 1)
            except (ValueError, TypeError):
                pass
        source_freshness.append({
            "source": r["source"],
            "last_ingest": last_dt,
            "hours_ago": hours_ago,
        })

    return {
        "status": "healthy",
        "database": {
            "total_facilities": fac,
            "total_violations": vio,
        },
        "last_ingest": last[0] if last else None,
        "source_freshness": sorted(source_freshness, key=lambda x: x["source"]),
        "checked_at": now.isoformat(),
    }


@public_router.get("/ping", summary="Liveness probe", include_in_schema=True)
def ping():
    """Zero-DB liveness probe for uptime monitors and the dashboard status indicator.

    Returns immediately without touching the database. Suitable for high-frequency
    external monitoring (UptimeRobot, Cloudflare Health Checks, etc.) and the
    dashboard status dot.  Use /api/v1/health for full readiness including DB
    connectivity and data freshness.
    """
    import time
    return {"ok": True, "ts": int(time.time())}
