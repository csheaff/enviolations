"""Map raw CA GeoTracker data to Pydantic models.

Data from data.ca.gov CKAN API (resource dc042197-e538-4a8b-9266-9c288aa72dcd):
  - GeoTracker Sites (77K records) → Facility + Violation

GeoTracker = State Water Resources Control Board database of LUST cleanup
sites, military cleanup sites, UST sites, land disposal sites, and other
groundwater contamination cases in California.

Each case record maps to a Facility. Open/active cases additionally map
to Violations (the ongoing contamination is the violation).

CASE_TYPE categories and their program area mappings:
  - "LUST Cleanup Site"         → LUST (Leaking UST)
  - "Cleanup Program Site"      → Cleanup
  - "Military Cleanup Site"     → Military Cleanup
  - "Military UST Site"         → Military / LUST
  - "Military Privatized Site"  → Military Cleanup
  - "Land Disposal Site"        → Land Disposal
  - "Single-Walled UST"         → UST
  - "Abandoned UST"             → UST
  - "Underground Injection Control (UIC)" → UIC
  - "Produced Water Ponds"      → Oil & Gas
  - "Non-Case Information"      → General
  - others                      → General
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, parse_date, normalize_ca_city


def _clean(val) -> str | None:
    return clean(val, sentinel=True)

SOURCE = "ca_geotracker"

# Case types that are considered cleanup/enforcement sites (not just informational)
_ENFORCEMENT_CASE_TYPES = {
    "LUST Cleanup Site",
    "Cleanup Program Site",
    "Military Cleanup Site",
    "Military UST Site",
    "Military Privatized Site",
    "Military UST Privatized Site",
    "Land Disposal Site",
    "Single-Walled UST",
    "Abandoned UST",
    "Underground Injection Control (UIC)",
    "Produced Water Ponds",
}

# Open/active statuses that indicate an ongoing violation
_OPEN_STATUSES = {
    "Open",
    "Open - Site Assessment",
    "Open - Remediation",
    "Open - Inactive",
    "Open - Verification Monitoring",
    "Open - Assessment & Interim Remedial Action",
    "Open - Eligible for Closure",
    "Open - Active",
    "Open - Long Term Management",
    "Open - Operating",
    "Open - Closed/with Monitoring",
    "Open - Sampling Point",
    "* Open - Sampling Point",
    "Pending Review",
    "Received",
    "Active",
    "In Compliance - USTs Removed",
    "USTs Permanently Closed - Report Pending",
}

# Mapping from CASE_TYPE to program_area label
_CASE_TYPE_TO_PROGRAM: dict[str, str] = {
    "LUST Cleanup Site": "LUST",
    "Cleanup Program Site": "Cleanup",
    "Military Cleanup Site": "Military Cleanup",
    "Military UST Site": "Military / LUST",
    "Military Privatized Site": "Military Cleanup",
    "Military UST Privatized Site": "Military / LUST",
    "Land Disposal Site": "Land Disposal",
    "Single-Walled UST": "UST",
    "Abandoned UST": "UST",
    "Underground Injection Control (UIC)": "UIC",
    "Produced Water Ponds": "Oil & Gas",
    "Non-Case Information": "General",
    "Project": "General",
    "Sampling Point - Public": "General",
    "Other Oil and Gas Projects": "Oil & Gas",
    "Well Stimulation Project - Exclusion": "Oil & Gas",
    "Well Stimulation Project - Groundwater Monitoring Plan": "Oil & Gas",
    "Aquifer Exemption": "General",
    "* NPDES": "Water Quality",
    "* Confined Animal Facilities (CAF)": "Agriculture",
    "Health Protection Zone Sampling": "General",
}


def _parse_date(val: str | None) -> date | None:
    return parse_date(val)


def _build_address(rec: dict) -> str | None:
    """Combine STREET_NUMBER and STREET_NAME into a single address string."""
    number = _clean(rec.get("STREET_NUMBER"))
    name = _clean(rec.get("STREET_NAME"))
    if number and name:
        return f"{number} {name}"
    return name or number


def _get_program_area(case_type: str | None) -> str:
    if not case_type:
        return "General"
    return _CASE_TYPE_TO_PROGRAM.get(case_type, "General")


def _get_severity(status: str | None, case_type: str | None) -> str | None:
    if not status:
        return None
    s = status.lower()
    # Active remediation or assessment = more serious
    if "remediation" in s or "assessment" in s:
        return "High"
    if "open" in s and case_type in ("LUST Cleanup Site", "Military Cleanup Site"):
        return "Medium"
    if "open" in s:
        return "Low"
    if "pending" in s or "received" in s:
        return "Low"
    return None


def map_site_facility(rec: dict) -> Facility:
    """Convert a GeoTracker site record to a Facility model.

    Key fields: GLOBAL_ID, BUSINESS_NAME, STREET_NUMBER, STREET_NAME,
    CITY, STATE, ZIP, COUNTY, LATITUDE, LONGITUDE, CASE_TYPE, STATUS.
    """
    global_id = (_clean(rec.get("GLOBAL_ID")) or "").replace(" ", "")
    case_type = _clean(rec.get("CASE_TYPE"))
    status = _clean(rec.get("STATUS"))

    # Build programs: normalized label first (e.g. "LUST", "UST") so the
    # dashboard LUST/UST filter (/\blust\b/, /\bust\b/) matches reliably,
    # followed by the raw CASE_TYPE and STATUS for display context.
    normalized_label = _get_program_area(case_type)  # e.g. "LUST", "UST", "Military Cleanup"
    programs_parts = []
    if normalized_label and normalized_label != "General":
        programs_parts.append(normalized_label)
    if case_type:
        programs_parts.append(case_type)
    if status:
        programs_parts.append(status)

    return Facility(
        source=SOURCE,
        source_id=global_id,
        name=_clean(rec.get("BUSINESS_NAME")) or "Unknown",
        address=_build_address(rec),
        city=normalize_ca_city(_clean(rec.get("CITY"))),
        state="CA",
        zip_code=_clean(rec.get("ZIP")),
        county=_clean(rec.get("COUNTY")),
        lat=parse_float(rec.get("LATITUDE")),
        lon=parse_float(rec.get("LONGITUDE")),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs_parts) if programs_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


def is_violation_record(rec: dict) -> bool:
    """Return True if this site record should generate a Violation.

    Only enforcement-type cases with open/active statuses generate violations.
    Closed, completed, or purely informational cases do not.
    """
    case_type = _clean(rec.get("CASE_TYPE"))
    status = _clean(rec.get("STATUS"))

    # Must be an enforcement case type
    if case_type not in _ENFORCEMENT_CASE_TYPES:
        return False

    # Must be open/active (not closed/completed)
    if not status:
        return False
    return status in _OPEN_STATUSES


def map_site_violation(rec: dict) -> Violation:
    """Convert a GeoTracker open site record to a Violation.

    Each open cleanup case represents an active contamination violation.
    The GLOBAL_ID links the violation back to its facility.

    Key fields: GLOBAL_ID, CASE_TYPE, STATUS, STATUS_DATE, BEGIN_DATE,
    POTENTIAL_CONTAMINANTS_OF_CONCERN, DISCHARGE_SOURCE, DISCHARGE_CAUSE.
    """
    global_id = (_clean(rec.get("GLOBAL_ID")) or "").replace(" ", "")
    case_type = _clean(rec.get("CASE_TYPE"))
    status = _clean(rec.get("STATUS"))

    # Build description
    desc_parts = []
    if case_type:
        desc_parts.append(case_type)
    if status:
        desc_parts.append(f"Status: {status}")
    contaminants = _clean(rec.get("POTENTIAL_CONTAMINANTS_OF_CONCERN"))
    if contaminants:
        desc_parts.append(f"Contaminants: {contaminants}")
    media = _clean(rec.get("POTENTIAL_MEDIA_OF_CONCERN"))
    if media:
        desc_parts.append(f"Media: {media}")
    source_val = _clean(rec.get("DISCHARGE_SOURCE"))
    if source_val:
        desc_parts.append(f"Source: {source_val}")
    cause = _clean(rec.get("DISCHARGE_CAUSE"))
    if cause:
        desc_parts.append(f"Cause: {cause}")

    # Statute/regulatory reference
    rb_case = _clean(rec.get("RB_CASE_NUMBER"))
    loc_case = _clean(rec.get("LOC_CASE_NUMBER"))
    statute = rb_case or loc_case

    # Use BEGIN_DATE as the violation date, fall back to STATUS_DATE
    violation_date = _parse_date(rec.get("BEGIN_DATE")) or _parse_date(rec.get("STATUS_DATE"))

    program_area = _get_program_area(case_type)

    return Violation(
        source=SOURCE,
        source_id=f"case-{global_id}",
        facility_source_id=global_id,
        facility_source=SOURCE,
        violation_type=case_type or "Cleanup Site",
        violation_date=violation_date,
        statute=statute,
        program_area=program_area,
        severity=_get_severity(status, case_type),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
