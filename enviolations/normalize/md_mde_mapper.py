"""Map raw Maryland MDE ArcGIS feature data to Pydantic models.

MD MDE data comes from ArcGIS MapServer layers at mdgeodata.md.gov and mde.geodata.md.gov.
Three facility datasets:
  - Significant Wastewater Treatment Plants (MD_PointSourceDischarges/MapServer/0)
    → Facility (NPDES_ID key)
  - Point Source Discharges (MD_PointSourceDischarges/MapServer/1)
    → Facility (NPDESID key)
  - BioSolid Permits (LMA_Resource_Management_Program/.../MapServer/1)
    → Facility (AI_ID_1 key)

Violation/enforcement datasets from Socrata (opendata.maryland.gov):
  - WSA Violations (jwx7-mgcz) — water/sewer violations by MDE Activity ID
  - WSA Enforcement Actions (qbwh-5vec) — consent orders, penalties
  - ARA Enforcement Actions (fpps-g5hi) — air quality enforcement

Coordinates come from geometry objects (outSR=4326).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, extract_arcgis_coords, parse_date

SOURCE = "md_mde"


def map_wwtp(feature: dict) -> Facility:
    """Convert a Significant WWTP feature to a Facility model.

    Key fields: NPDES_ID, FACILITY_N, COUNTY_NAM, MAJOR_MINO, FACILITY_T,
    PERMIT_NAM.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    npdes_id = clean(attrs.get("NPDES_ID")) or ""
    facility_type = clean(attrs.get("FACILITY_T"))
    major_minor = clean(attrs.get("MAJOR_MINO"))
    programs = []
    if facility_type:
        programs.append(facility_type)
    if major_minor:
        programs.append(major_minor)

    return Facility(
        source=SOURCE,
        source_id=f"wwtp-{npdes_id}",
        name=clean(attrs.get("FACILITY_N")) or "Unknown",
        address=None,
        city=None,
        state="MD",
        zip_code=None,
        county=clean(attrs.get("COUNTY_NAM")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "WWTP",
        last_updated=datetime.now(timezone.utc),
    )


def map_point_source(feature: dict) -> Facility:
    """Convert a Point Source Discharges feature to a Facility model.

    Key fields: NPDESID, FAC_NAME, Addr1, Addr2, PermitCate, OwnerType,
    Facility_T, MDStateNum.
    Note: County field is a SmallInt (FIPS code), not a name.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    npdes_id = clean(attrs.get("NPDESID")) or ""
    permit_cat = clean(attrs.get("PermitCate"))
    facility_type = clean(attrs.get("Facility_T"))
    programs = []
    if permit_cat:
        programs.append(permit_cat)
    if facility_type:
        programs.append(facility_type)

    return Facility(
        source=SOURCE,
        source_id=f"npdes-{npdes_id}",
        name=clean(attrs.get("FAC_NAME")) or "Unknown",
        address=clean(attrs.get("Addr1")),
        city=None,
        state="MD",
        zip_code=None,
        county=None,  # County is FIPS SmallInt, not name
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Point Source",
        last_updated=datetime.now(timezone.utc),
    )


def map_biosolid(feature: dict) -> Facility:
    """Convert a BioSolid Permits feature to a Facility model.

    Key fields: AI_ID_1, FACILITY_1, PERMIT_C_1, COUNTY_12.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    ai_id = clean(attrs.get("AI_ID_1")) or ""
    permit_cat = clean(attrs.get("PERMIT_C_1"))

    return Facility(
        source=SOURCE,
        source_id=f"bio-{ai_id}",
        name=clean(attrs.get("FACILITY_1")) or "Unknown",
        address=None,
        city=None,
        state="MD",
        zip_code=None,
        county=clean(attrs.get("COUNTY_12")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=permit_cat or "BioSolid",
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Socrata violation/enforcement mappers
# ---------------------------------------------------------------------------


def _parse_city_state_zip(val: str | None) -> tuple[str | None, str | None]:
    """Parse 'City,MD,21201' into (city, zip_code)."""
    if not val:
        return None, None
    parts = [p.strip() for p in val.split(",")]
    city = parts[0] if parts else None
    zip_code = parts[2] if len(parts) >= 3 else None
    return city, zip_code


def _parse_date(val: str | None) -> date | None:
    return parse_date(val)


def map_wsa_facility(record: dict) -> Facility:
    """Create facility from WSA violation or enforcement record (keyed by ai_id)."""
    ai_id = clean(record.get("ai_id")) or ""
    city, zip_code = _parse_city_state_zip(record.get("city_state_zip"))
    program = clean(record.get("program")) or clean(record.get("media"))

    return Facility(
        source=SOURCE,
        source_id=f"wsa-{ai_id}",
        name=clean(record.get("ai_name")) or "Unknown",
        address=clean(record.get("addressinfo")),
        city=city,
        state="MD",
        zip_code=zip_code,
        county=clean(record.get("county")),
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs=program or "WSA",
        last_updated=datetime.now(timezone.utc),
    )


def map_wsa_violation(record: dict) -> Violation:
    """Map WSA Violations record (jwx7-mgcz) to Violation model."""
    ai_id = clean(record.get("ai_id")) or ""
    enf_issue = clean(record.get("enf_issue"))
    violation_dt = _parse_date(record.get("violation_date_snc_date"))
    resolved_dt = _parse_date(record.get("resolved_date"))

    severity = "Open" if resolved_dt is None else "Resolved"

    return Violation(
        source=SOURCE,
        source_id=f"wsav-{ai_id}-{violation_dt or 'nd'}",
        facility_source_id=f"wsa-{ai_id}",
        facility_source=SOURCE,
        violation_type=enf_issue or "WSA Violation",
        violation_date=violation_dt,
        statute=None,
        program_area="Water",
        severity=severity,
        description=enf_issue,
        last_updated=datetime.now(timezone.utc),
    )


def map_wsa_enforcement(record: dict) -> Violation:
    """Map WSA Enforcement Actions record (qbwh-5vec) to Violation model."""
    ai_id = clean(record.get("ai_id")) or ""
    action = clean(record.get("enforcement_action"))
    action_no = clean(record.get("enforcement_action_no")) or ""
    issued_dt = _parse_date(record.get("enforcement_action_issued"))
    closed_dt = _parse_date(record.get("case_closed"))
    media = clean(record.get("media"))
    program = clean(record.get("program"))

    severity = "Open" if closed_dt is None else "Resolved"
    if action and "penalty" in action.lower():
        severity = "Significant" if closed_dt is None else severity

    desc_parts = [p for p in [action, media, program] if p]

    return Violation(
        source=SOURCE,
        source_id=f"wsae-{action_no}" if action_no else f"wsae-{ai_id}-{issued_dt or 'nd'}",
        facility_source_id=f"wsa-{ai_id}",
        facility_source=SOURCE,
        violation_type=action or "WSA Enforcement",
        violation_date=issued_dt,
        statute=None,
        program_area=media or "Water",
        severity=severity,
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


def map_ara_facility(record: dict) -> Facility:
    """Create facility from ARA (Air) enforcement record (keyed by ai field)."""
    ai = clean(record.get("ai")) or ""
    city, zip_code = _parse_city_state_zip(record.get("city_state_zip"))

    return Facility(
        source=SOURCE,
        source_id=f"ara-{ai}",
        name=clean(record.get("facility_name")) or "Unknown",
        address=clean(record.get("addressinfo")),
        city=city,
        state="MD",
        zip_code=zip_code,
        county=clean(record.get("county")),
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs="Air Quality",
        last_updated=datetime.now(timezone.utc),
    )


def map_ara_enforcement(record: dict) -> Violation:
    """Map ARA Enforcement Actions record (fpps-g5hi) to Violation model."""
    ai = clean(record.get("ai")) or ""
    action_desc = clean(record.get("action_description"))
    achieved_dt = _parse_date(record.get("achieved_date"))
    facility_name = clean(record.get("facility_name"))

    # Severity from action type codes
    severity = None
    if action_desc:
        desc_lower = action_desc.lower()
        if "hpv" in desc_lower or "penalty" in desc_lower:
            severity = "Significant"
        elif "warning" in desc_lower or "notice" in desc_lower:
            severity = "Medium"

    return Violation(
        source=SOURCE,
        source_id=f"arae-{ai}-{achieved_dt or 'nd'}",
        facility_source_id=f"ara-{ai}",
        facility_source=SOURCE,
        violation_type=action_desc or "Air Enforcement",
        violation_date=achieved_dt,
        statute=None,
        program_area="Air",
        severity=severity,
        description=f"{action_desc}; {facility_name}" if action_desc and facility_name else action_desc,
        last_updated=datetime.now(timezone.utc),
    )
