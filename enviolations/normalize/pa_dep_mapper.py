"""Map raw PA DEP Socrata CSV rows to Pydantic models.

PA DEP data is downloaded as CSV from data.pa.gov. The csv.DictReader
produces keys matching the CSV column headers. This mapper handles three
datasets:
  - EIS Facilities (air emission plants) → Facility
  - Safe Drinking Water Facilities → Facility
  - Oil & Gas Well Inspections → Violation
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, parse_date

SOURCE = "pa_dep"

# Inspection results that indicate a violation was found
_VIOLATION_RESULTS = {
    "Violation(s) Noted",
    "Outstanding Violations - Viols Req'd",
    "Viol(s) Noted & Immediately Corrected",
    "Outstanding Violations - No Viols Req'd",
    "Violation(s) & Outstanding Violations",
}


def _parse_date(val: str | None) -> date | None:
    return parse_date(val, formats=("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y"))


def map_eis_facility(row: dict) -> Facility:
    """Convert an EIS Facilities CSV row to a Facility model.

    CSV headers: 'Facility Site & Associated System Identifier',
    'Facility Site Name', 'Location Address', 'Locality Name',
    'Location County Name', 'Location Address State Code',
    'Location Address Postal Code', 'Latitude Measure',
    'Longitude Measure', 'NAICS Code', etc.
    """
    site_id = clean(row.get("Facility Site & Associated System Identifier")) or ""
    name = clean(row.get("Facility Site Name")) or "Unknown"

    return Facility(
        source=SOURCE,
        source_id=f"eis-{site_id}",
        name=name,
        address=clean(row.get("Location Address")),
        city=clean(row.get("Locality Name")),
        state=clean(row.get("Location Address State Code")) or "PA",
        zip_code=clean(row.get("Location Address Postal Code")),
        county=clean(row.get("Location County Name")),
        lat=parse_float(row.get("Latitude Measure")),
        lon=parse_float(row.get("Longitude Measure")),
        naics_codes=clean(row.get("NAICS Code")),
        sic_codes=None,
        programs="CAA",
        last_updated=datetime.now(timezone.utc),
    )


def map_sdwa_facility(row: dict) -> Facility:
    """Convert a Safe Drinking Water Facilities CSV row to a Facility model.

    CSV headers: 'PA Water System Identifier', 'Water System Name',
    'Served County Name', 'County Centroid Latitude',
    'County Centroid Longitude', etc.
    """
    pws_id = clean(row.get("PA Water System Identifier")) or ""
    name = clean(row.get("Water System Name")) or "Unknown"

    return Facility(
        source=SOURCE,
        source_id=f"sdwa-{pws_id}",
        name=name,
        address=None,
        city=None,
        state="PA",
        zip_code=None,
        county=clean(row.get("Served County Name")),
        lat=parse_float(row.get("County Centroid Latitude")),
        lon=parse_float(row.get("County Centroid Longitude")),
        naics_codes=None,
        sic_codes=None,
        programs=clean(row.get("PA Water System Federal Type")),
        last_updated=datetime.now(timezone.utc),
    )


def map_well_facility(row: dict) -> Facility:
    """Create a minimal Facility stub for an O&G well permit.

    Each unique PERMIT from the inspection dataset gets one facility so
    O&G inspection violations can link via facility_source_id=well-{permit}.
    """
    permit = clean(row.get("PERMIT")) or ""
    client = clean(row.get("CLIENT")) or "Unknown"
    county = clean(row.get("COUNTY"))

    return Facility(
        source=SOURCE,
        source_id=f"well-{permit}",
        name=client,
        address=None,
        city=None,
        state="PA",
        zip_code=None,
        county=county,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs="Oil & Gas",
        last_updated=datetime.now(timezone.utc),
    )


def is_violation_result(result_desc: str | None) -> bool:
    """Return True if the inspection result indicates a violation."""
    if not result_desc:
        return False
    return result_desc.strip() in _VIOLATION_RESULTS


def _severity_from_result(result_desc: str | None) -> str | None:
    """Derive severity from inspection result description."""
    if not result_desc:
        return None
    result = result_desc.strip()
    if result in ("Outstanding Violations - Viols Req'd", "Violation(s) & Outstanding Violations"):
        return "High"
    if result == "Violation(s) Noted":
        return "Medium"
    if result in ("Viol(s) Noted & Immediately Corrected", "Outstanding Violations - No Viols Req'd"):
        return "Low"
    return None


def map_inspection(row: dict) -> Violation:
    """Convert an Oil & Gas Well Inspection CSV row to a Violation model.

    CSV headers: 'PERMIT', 'CLIENT', 'INSPECTION_ID', 'INSPECTION_DATE',
    'INSPECTION_TYPE', 'INSPECTION_RESULT_DESC', 'COUNTY', etc.
    """
    inspection_id = clean(row.get("INSPECTION_ID")) or ""
    permit = clean(row.get("PERMIT")) or ""
    result_desc = clean(row.get("INSPECTION_RESULT_DESC"))

    return Violation(
        source=SOURCE,
        source_id=f"insp-{inspection_id}",
        facility_source_id=f"well-{permit}",
        facility_source=SOURCE,
        violation_type="INSPECTION",
        violation_date=_parse_date(row.get("INSPECTION_DATE")),
        statute=None,
        program_area=clean(row.get("INSPECTION_TYPE")),
        severity=_severity_from_result(result_desc),
        description=result_desc,
        last_updated=datetime.now(timezone.utc),
    )
