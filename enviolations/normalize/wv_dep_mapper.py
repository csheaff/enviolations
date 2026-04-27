"""Map raw West Virginia DEP ArcGIS feature data to Pydantic models.

WV DEP data comes from TAGIS ArcGIS Enterprise at tagis.dep.wv.gov.
Facility datasets:
  - Landfills (waste_management layer 0): facility, county, type, latitude, longitude
  - Voluntary Remediation Sites (environmental_remediation layer 2): proj_id, fac_name,
    latitude, longitude, contamination, proj_status
  - SEMS Sites (environmental_remediation layer 14): site_name, street_add, city, county,
    zip_code, latitude, longitude, epa_id, contaminat
  - Air Quality All Facilities (air_quality layer 7): pm_cid, pm_cname, ad_cstreet,
    ad_dd_lat, ad_dd_lon, naic_code
Violation datasets:
  - Mining Permits Inspection Status (mining_reclamation layer 9) → facilities + violations
    (4.4K permits, ~2.1K with violations; violation counts per permit)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import parse_float, clean, epoch_ms_to_date

SOURCE = "wv_dep"

def map_landfill(feature: dict) -> Facility:
    """Convert a waste_management landfill feature to a Facility."""
    attrs = feature.get("attributes", {})

    row_id = clean(attrs.get("row")) or str(attrs.get("objectid", ""))
    facility_name = clean(attrs.get("facility")) or "Unknown"
    source_id = f"landfill-{row_id}"

    lat = parse_float(attrs.get("latitude"), zero_as_none=True)
    lon = parse_float(attrs.get("longitude"), zero_as_none=True)
    if not lat or not lon:
        geom = feature.get("geometry")
        if geom:
            lon = parse_float(geom.get("x"), zero_as_none=True)
            lat = parse_float(geom.get("y"), zero_as_none=True)

    programs = ["Landfill"]
    lf_type = clean(attrs.get("type"))
    if lf_type:
        programs.append(lf_type)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=facility_name,
        address=None,
        city=None,
        state="WV",
        zip_code=None,
        county=clean(attrs.get("county")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_vr_site(feature: dict) -> Facility:
    """Convert a Voluntary Remediation Sites feature to a Facility."""
    attrs = feature.get("attributes", {})

    proj_id = clean(attrs.get("proj_id"))
    source_id = f"vr-{proj_id}" if proj_id else f"vr-{attrs.get('objectid', '')}"

    lat = parse_float(attrs.get("latitude"), zero_as_none=True)
    lon = parse_float(attrs.get("longitude"), zero_as_none=True)
    if not lat or not lon:
        geom = feature.get("geometry")
        if geom:
            lon = parse_float(geom.get("x"), zero_as_none=True)
            lat = parse_float(geom.get("y"), zero_as_none=True)

    programs = ["Voluntary Remediation"]
    status = clean(attrs.get("proj_status"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("fac_name")) or clean(attrs.get("proj_name")) or "Unknown",
        address=None,
        city=None,
        state="WV",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_sems_site(feature: dict) -> Facility:
    """Convert a SEMS Sites feature to a Facility."""
    attrs = feature.get("attributes", {})

    wv_id = clean(attrs.get("wv_id_")) or ""
    epa_id = clean(attrs.get("epa_id")) or ""
    source_id = f"sems-{wv_id}" if wv_id else f"sems-{epa_id or attrs.get('objectid', '')}"

    lat = parse_float(attrs.get("latitude"), zero_as_none=True)
    lon = parse_float(attrs.get("longitude"), zero_as_none=True)
    if not lat or not lon:
        geom = feature.get("geometry")
        if geom:
            lon = parse_float(geom.get("x"), zero_as_none=True)
            lat = parse_float(geom.get("y"), zero_as_none=True)

    programs = ["SEMS"]
    npl = clean(attrs.get("npl_status"))
    if npl:
        programs.append(f"NPL: {npl}")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("site_name")) or "Unknown",
        address=clean(attrs.get("street_add")),
        city=clean(attrs.get("city")),
        state="WV",
        zip_code=clean(attrs.get("zip_code")),
        county=clean(attrs.get("county")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_air_facility(feature: dict) -> Facility:
    """Convert an air_quality All Facilities feature to a Facility."""
    attrs = feature.get("attributes", {})

    pm_cid = clean(attrs.get("pm_cid")) or ""
    source_id = f"air-{pm_cid}" if pm_cid else f"air-{attrs.get('objectid', '')}"

    lat = parse_float(attrs.get("ad_dd_lat"), zero_as_none=True)
    lon = parse_float(attrs.get("ad_dd_lon"), zero_as_none=True)
    if not lat or not lon:
        geom = feature.get("geometry")
        if geom:
            lon = parse_float(geom.get("x"), zero_as_none=True)
            lat = parse_float(geom.get("y"), zero_as_none=True)

    naic = clean(attrs.get("naic_code"))
    naics_str = str(naic) if naic else None

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("pm_cname")) or "Unknown",
        address=clean(attrs.get("ad_cstreet")),
        city=clean(attrs.get("ad_city")),
        state="WV",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=naics_str,
        sic_codes=None,
        programs="Air",
        last_updated=datetime.now(timezone.utc),
    )

# ---------------------------------------------------------------------------
# Mining Permits Inspection Status (mining_reclamation/MapServer/9)
# ---------------------------------------------------------------------------

def _polygon_centroid(feature: dict) -> tuple[float | None, float | None]:
    """Extract approximate centroid from an ArcGIS polygon geometry.

    Returns (lat, lon) by averaging ring coordinates.  Falls back to None.
    """
    geom = feature.get("geometry")
    if not geom:
        return None, None
    rings = geom.get("rings")
    if not rings:
        return None, None
    xs, ys = [], []
    for ring in rings:
        for coord in ring:
            if len(coord) >= 2:
                xs.append(coord[0])
                ys.append(coord[1])
    if not xs:
        return None, None
    lon = sum(xs) / len(xs)
    lat = sum(ys) / len(ys)
    return lat, lon

def _mining_severity(attrs: dict) -> str | None:
    """Derive severity from mining permit violation counts and status.

    active_vio >= 5 → High (severe ongoing noncompliance)
    active_vio >= 1 → Medium (active violations)
    total_vio > 0 and active_vio == 0 → Low (historical only)
    Revoked permits (mstatus=RV) with violations → High
    """
    active = attrs.get("active_vio") or 0
    total = attrs.get("total_vio") or 0
    mstatus = clean(attrs.get("mstatus")) or ""

    if active >= 5 or (mstatus == "RV" and total > 0):
        return "High"
    if active >= 1:
        return "Medium"
    if total > 0:
        return "Low"
    return None

def map_mining_facility(feature: dict) -> Facility:
    """Convert a mining_reclamation Inspection Status feature to a Facility.

    Key fields: permit_id, facility_name, permittee, operator, pstatus,
    acres_current, issue_date, expire_date.
    """
    attrs = feature.get("attributes", {})

    permit_id = clean(attrs.get("permit_id")) or ""

    lat, lon = _polygon_centroid(feature)

    programs = ["Mining"]
    pstatus = clean(attrs.get("pstatus"))
    if pstatus:
        programs.append(pstatus)

    name = (clean(attrs.get("facility_name"))
            or clean(attrs.get("permittee"))
            or clean(attrs.get("operator"))
            or "Unknown")

    return Facility(
        source=SOURCE,
        source_id=f"mining-{permit_id}",
        name=name,
        address=None,
        city=None,
        state="WV",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_mining_violation(feature: dict) -> Violation:
    """Convert a mining permit with violations to a Violation.

    Creates one violation per mining permit that has total_vio > 0.
    Description includes active/total violation counts, permit status,
    and operator information.
    """
    attrs = feature.get("attributes", {})

    permit_id = clean(attrs.get("permit_id")) or ""
    active = attrs.get("active_vio") or 0
    total = attrs.get("total_vio") or 0

    desc_parts = []
    facility_name = clean(attrs.get("facility_name"))
    if facility_name:
        desc_parts.append(facility_name)
    desc_parts.append(f"Active violations: {active}")
    desc_parts.append(f"Total violations: {total}")
    mstatus = clean(attrs.get("mstatus"))
    if mstatus:
        desc_parts.append(f"Inspection status: {mstatus}")
    operator = clean(attrs.get("operator"))
    if operator:
        desc_parts.append(f"Operator: {operator}")
    permittee = clean(attrs.get("permittee"))
    if permittee and permittee != operator:
        desc_parts.append(f"Permittee: {permittee}")

    return Violation(
        source=SOURCE,
        source_id=f"mining-{permit_id}",
        facility_source_id=f"mining-{permit_id}",
        facility_source=SOURCE,
        violation_type="Mining Violation",
        violation_date=epoch_ms_to_date(attrs.get("mdate")),
        statute=None,
        program_area="Mining",
        severity=_mining_severity(attrs),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
