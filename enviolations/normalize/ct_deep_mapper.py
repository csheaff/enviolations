"""Map raw Connecticut DEEP Socrata data to Pydantic models.

CT DEEP data comes from data.ct.gov Socrata portal:
  - UST Facility & Tank Details (utni-rddb): tank-level, deduped by agencyfacilityid
    Fields: agencyfacilityid, facilitynm, facilityaddr, facilitycity, facilityzip,
            ust_site_latitude, ust_site_longitude
  - Contaminated Sites / Remediation (u76p-weqj): site-level
    Fields: rem_id, site_name, address, town, programname
  - Formal Enforcement Case Summaries (t2bf-45ba): 2021-present
    Fields: date_issued, respondent, town, violation_address, type_of_enforcement,
    violation_citation, violation_description, penalty_amount, deep_program, order_number
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float

SOURCE = "ct_deep"


def map_ust_facility(record: dict) -> Facility:
    """Convert a UST Facility & Tank Details Socrata record to a Facility.

    Records are tank-level — caller should dedup by agencyfacilityid.
    """
    fac_id = clean(record.get("agencyfacilityid")) or ""

    lat = parse_float(record.get("ust_site_latitude"))
    lon = parse_float(record.get("ust_site_longitude"))
    if lat is None:
        lat = parse_float(record.get("tanklat"))
    if lon is None:
        lon = parse_float(record.get("tanklon"))

    return Facility(
        source=SOURCE,
        source_id=f"ust-{fac_id}",
        name=clean(record.get("facilitynm")) or "Unknown",
        address=clean(record.get("facilityaddr")),
        city=clean(record.get("facilitycity")),
        state="CT",
        zip_code=clean(record.get("facilityzip")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="UST",
        last_updated=datetime.now(timezone.utc),
    )


def map_remediation_site(record: dict) -> Facility:
    """Convert a Remediation Division Socrata record to a Facility."""
    rem_id = clean(record.get("rem_id")) or ""
    program = clean(record.get("programname"))

    return Facility(
        source=SOURCE,
        source_id=f"rem-{rem_id}",
        name=clean(record.get("site_name")) or "Unknown",
        address=clean(record.get("address")),
        city=clean(record.get("town")),
        state="CT",
        zip_code=None,
        county=None,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs=program or "Remediation",
        last_updated=datetime.now(timezone.utc),
    )


def _parse_enforcement_date(val) -> date | None:
    """Parse Socrata ISO timestamp to date."""
    if not val:
        return None
    try:
        return date.fromisoformat(str(val)[:10])
    except (ValueError, TypeError):
        return None


def _enforcement_facility_id(respondent: str, town: str) -> str:
    """Generate a stable facility ID from respondent + town."""
    key = re.sub(r"[^a-z0-9]", "", f"{respondent}{town}".lower())
    return f"enf-{key[:60]}"


def map_enforcement_facility(record: dict) -> Facility:
    """Create a facility record from an enforcement case respondent."""
    respondent = clean(record.get("respondent")) or "Unknown"
    town = clean(record.get("town")) or ""
    fac_id = _enforcement_facility_id(respondent, town)

    return Facility(
        source=SOURCE,
        source_id=fac_id,
        name=respondent,
        address=clean(record.get("violation_address")),
        city=town if town else None,
        state="CT",
        zip_code=None,
        county=None,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs=clean(record.get("deep_program")) or "Enforcement",
        last_updated=datetime.now(timezone.utc),
    )


def map_enforcement_violation(record: dict) -> Violation:
    """Convert a CT DEEP enforcement case to a Violation model."""
    respondent = clean(record.get("respondent")) or "Unknown"
    town = clean(record.get("town")) or ""
    fac_id = _enforcement_facility_id(respondent, town)
    order_num = clean(record.get("order_number")) or ""
    order_key = re.sub(r"\.pdf$", "", order_num, flags=re.IGNORECASE)

    penalty = clean(record.get("penalty_amount"))
    desc_parts = []
    citation = clean(record.get("violation_citation"))
    vio_desc = clean(record.get("violation_description"))
    if vio_desc:
        desc_parts.append(vio_desc)
    if citation:
        desc_parts.append(f"Citation: {citation}")
    if penalty:
        desc_parts.append(f"Penalty: ${penalty}")

    return Violation(
        source=SOURCE,
        source_id=f"enf-{order_key}",
        facility_source_id=fac_id,
        facility_source=SOURCE,
        violation_type=clean(record.get("type_of_enforcement")) or "Enforcement",
        violation_date=_parse_enforcement_date(record.get("date_issued")),
        statute=citation,
        program_area=clean(record.get("deep_program")),
        severity="Formal Enforcement",
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
