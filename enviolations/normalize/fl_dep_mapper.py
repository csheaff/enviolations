"""Map raw FL DEP ArcGIS feature attributes to Pydantic models.

FL DEP data comes from ArcGIS REST API at ca.dep.state.fl.us. This mapper
handles three facility datasets:
  - WAFR Wastewater Facilities → Facility
  - ERIC Waste Cleanup Sites → Facility
  - CHAZ Hazardous Waste Facilities (LQGs) → Facility

Also handles Coastal Permit Violations (MapServer/11):
  - VIOLATION_COMPLIANCE_NUM, LETTER_SENT, DESCRIPTION_1, PERMIT_LIST,
    FIRST_VIOLATOR_FULLNAME, FINED, FIRST_LOCATION, SUMMARY_REPORT

Note: FL DEP stores coordinates as degrees/minutes/seconds (DMS) which
must be converted to decimal degrees.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, extract_arcgis_coords, epoch_ms_to_date

SOURCE = "fl_dep"


def _dms_to_decimal(dd, mm, ss) -> float | None:
    """Convert degrees/minutes/seconds to decimal degrees."""
    d = parse_float(dd)
    m = parse_float(mm)
    s = parse_float(ss)
    if d is None:
        return None
    m = m or 0.0
    s = s or 0.0
    sign = -1 if d < 0 else 1
    return sign * (abs(d) + m / 60.0 + s / 3600.0)


def _county_name_from_id(county_id) -> str | None:
    """FL DEP uses county FIPS codes in some datasets. Return as-is for now."""
    val = clean(county_id)
    return val


def map_wafr_facility(attrs: dict) -> Facility:
    """Convert WAFR Wastewater Facility feature attributes to a Facility model.

    Key fields: FACILITY_ID, FACILITY_NAME, COUNTY_NAME,
    LAT_DD/LAT_MM/LAT_SS, LONG_DD/LONG_MM/LONG_SS,
    PERMIT_TYPE, FACILITY_STATUS, FACILITY_TYPE_CODE.
    """
    fac_id = clean(attrs.get("FACILITY_ID")) or ""

    lat = _dms_to_decimal(attrs.get("LAT_DD"), attrs.get("LAT_MM"), attrs.get("LAT_SS"))
    lon = _dms_to_decimal(attrs.get("LONG_DD"), attrs.get("LONG_MM"), attrs.get("LONG_SS"))
    # FL longitudes are west, ensure negative
    if lon is not None and lon > 0:
        lon = -lon

    return Facility(
        source=SOURCE,
        source_id=f"wafr-{fac_id}",
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=None,
        city=None,
        state="FL",
        zip_code=None,
        county=clean(attrs.get("COUNTY_NAME")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("PERMIT_TYPE")) or clean(attrs.get("FACILITY_TYPE_CODE")),
        last_updated=datetime.now(timezone.utc),
    )


def map_eric_site(attrs: dict) -> Facility:
    """Convert ERIC Waste Cleanup site feature attributes to a Facility model.

    Key fields: SOURCE_FACILITY_ID, SOURCE_FACILITY_NAME, ERIC_ID,
    ADDRESS, CITY, COUNTY_NAME, ZIP,
    LAT_DD/LAT_MM/LAT_SS, LONG_DD/LONG_MM/LONG_SS,
    PROGRAM, PROGRAM_STATUS.
    """
    fac_id = clean(attrs.get("SOURCE_FACILITY_ID")) or clean(attrs.get("ERIC_ID")) or ""

    lat = _dms_to_decimal(attrs.get("LAT_DD"), attrs.get("LAT_MM"), attrs.get("LAT_SS"))
    lon = _dms_to_decimal(attrs.get("LONG_DD"), attrs.get("LONG_MM"), attrs.get("LONG_SS"))
    if lon is not None and lon > 0:
        lon = -lon

    return Facility(
        source=SOURCE,
        source_id=f"eric-{fac_id}",
        name=clean(attrs.get("SOURCE_FACILITY_NAME")) or clean(attrs.get("SITE_NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY_NAME")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("PROGRAM")),
        last_updated=datetime.now(timezone.utc),
    )


def map_chaz_facility(attrs: dict) -> Facility:
    """Convert CHAZ Hazardous Waste facility feature attributes to a Facility model.

    Key fields: HANDLER_ID, NAME, ADDRESS, CITY, ZIP5, COUNTY_NAME,
    OFFICE, FAC_INS_TYPE.
    """
    handler_id = clean(attrs.get("HANDLER_ID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"chaz-{handler_id}",
        name=clean(attrs.get("NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=clean(attrs.get("ZIP5")),
        county=clean(attrs.get("COUNTY_NAME")),
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("FAC_INS_TYPE")),
        last_updated=datetime.now(timezone.utc),
    )


def map_coastal_violation_facility(feature: dict) -> Facility:
    """Create a facility from a coastal permit violation's violator info."""
    attrs = feature.get("attributes", {})
    vio_num = clean(attrs.get("VIOLATION_COMPLIANCE_NUM")) or ""
    name = clean(attrs.get("FIRST_VIOLATOR_FULLNAME")) or "Unknown"
    lat, lon = extract_arcgis_coords(feature)

    return Facility(
        source=SOURCE,
        source_id=f"cpv-{vio_num}",
        name=name,
        address=clean(attrs.get("FIRST_LOCATION")),
        city=None,
        state="FL",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Coastal Permit",
        last_updated=datetime.now(timezone.utc),
    )


def map_coastal_violation(feature: dict) -> Violation:
    """Convert a FL DEP Coastal Permit Violation to a Violation model."""
    attrs = feature.get("attributes", {})
    vio_num = clean(attrs.get("VIOLATION_COMPLIANCE_NUM")) or ""

    desc_parts = []
    desc = clean(attrs.get("DESCRIPTION_1"))
    summary = clean(attrs.get("SUMMARY_REPORT"))
    permit = clean(attrs.get("PERMIT_LIST"))
    if desc:
        desc_parts.append(desc)
    if summary:
        desc_parts.append(summary)
    if permit:
        desc_parts.append(f"Permit: {permit}")

    fined = clean(attrs.get("FINED"))
    severity = "Fined" if fined and fined.upper() == "YES" else "Violation"

    return Violation(
        source=SOURCE,
        source_id=f"cpv-{vio_num}",
        facility_source_id=f"cpv-{vio_num}",
        facility_source=SOURCE,
        violation_type="Coastal Permit Violation",
        violation_date=epoch_ms_to_date(attrs.get("LETTER_SENT")),
        statute=None,
        program_area="Coastal",
        severity=severity,
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
