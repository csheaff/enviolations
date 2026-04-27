"""Map raw Arkansas DEQ ArcGIS feature data to Pydantic models.

AR DEQ data comes from ArcGIS FeatureServer at gis.arkansas.gov:
  - FACILITIES_DEQ (Layer 0): afin, fname, fsiteaddr2, fsiteaddr3,
    fsitecity, fsitestate, fsitezip, fcounty, flatdec, flongdec,
    ownername, fpnaicsc, fpnaicsd

  - Inspections (MapServer/3) from gis.adeq.state.ar.us:
    InspAFIN, InspNbrFormatted, InspDate, InspName, InspCity, InspZip,
    InspMediaDesc, InspComplianceStatusDesc, InspConcernsComment

Coordinates come from explicit flatdec/flongdec attribute fields or geometry.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import parse_float, clean, epoch_ms_to_date, extract_arcgis_coords

SOURCE = "ar_deq"

def map_facility(feature: dict) -> Facility:
    """Convert a FACILITIES_DEQ feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    afin = clean(attrs.get("afin")) or clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("flatdec"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("flongdec"), zero_as_none=True)

    # Build address from multiple fields
    addr2 = clean(attrs.get("fsiteaddr2"))
    addr3 = clean(attrs.get("fsiteaddr3"))
    address = addr2 or addr3

    # NAICS codes
    naics = clean(attrs.get("fpnaicsc"))

    return Facility(
        source=SOURCE,
        source_id=f"fac-{afin}",
        name=clean(attrs.get("fname")) or "Unknown",
        address=address,
        city=clean(attrs.get("fsitecity")),
        state="AR",
        zip_code=clean(attrs.get("fsitezip")),
        county=clean(attrs.get("fcounty")),
        lat=lat,
        lon=lon,
        naics_codes=naics,
        programs="Regulated Facility",
        last_updated=datetime.now(timezone.utc),
    )

def has_violation(feature: dict) -> bool:
    """Check whether an AR DEQ inspection represents an actual violation.

    ~55% of inspection records are clean compliance checks ('In Compliance',
    'Inspection', 'No Violations Found', 'Not in Operation'). Only keep
    records that indicate actual non-compliance.
    """
    attrs = feature.get("attributes", {})
    status = (clean(attrs.get("InspComplianceStatusDesc")) or "").lower()
    # These are definitively NOT violations
    non_violation = {
        "in compliance",
        "inspection",
        "no violations found",
        "not in operation",
        # SOC statuses that indicate compliance
        "soc - meets detection and prevention",
    }
    return status not in non_violation and status != ""

def map_inspection(feature: dict) -> Violation:
    """Convert an AR DEQ inspection feature to a Violation model.

    Only call this after has_violation() returns True.
    """
    attrs = feature.get("attributes", {})

    afin = clean(attrs.get("InspAFIN")) or ""
    insp_nbr = clean(attrs.get("InspNbrFormatted")) or ""
    compliance = clean(attrs.get("InspComplianceStatusDesc"))
    media = clean(attrs.get("InspMediaDesc"))
    concerns = clean(attrs.get("InspConcernsComment"))
    general = clean(attrs.get("InspGeneralComment"))

    # Build description from available fields
    desc_parts = []
    if concerns:
        desc_parts.append(concerns)
    if general and len(general) < 500:
        desc_parts.append(general)

    return Violation(
        source=SOURCE,
        source_id=f"insp-{insp_nbr}",
        facility_source_id=f"fac-{afin}",
        facility_source=SOURCE,
        violation_type=compliance or "Inspection",
        violation_date=epoch_ms_to_date(attrs.get("InspDate")),
        statute=None,
        program_area=media,
        severity=compliance,
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
