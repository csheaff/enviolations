"""Map raw FL DEP STCM ArcGIS feature attributes to Pydantic models.

FL DEP Storage Tank Contamination Monitoring data comes from ArcGIS REST API
at ca.dep.state.fl.us. This mapper handles three datasets:
  - DWM_STCM/MapServer/1: Registered Tanks → Facility
  - DWM_STCM/MapServer/2: PCTS Discharges → Violation (+ Facility stub)
  - DWM_STCM/MapServer/4: Drycleaning Solvent Program Sites → Facility

Note: FL DEP stores coordinates as degrees/minutes/seconds (DMS) which
must be converted to decimal degrees.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, epoch_ms_to_date, parse_float

SOURCE = "fl_dep_stcm"


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


def map_stcm_facility(attrs: dict) -> Facility:
    """Convert STCM Registered Tank feature attributes to a Facility model.

    Key fields: FACILITY_ID, FACILITY_NAME, ADDRESS1, CITY, ZIP5, COUNTY,
    LAT_DD/LAT_MM/LAT_SS, LONG_DD/LONG_MM/LONG_SS,
    FACILITY_TYPE, FACILITY_STATUS, REGULATED, CLEANUP_STATUS.
    """
    fac_id = clean(attrs.get("FACILITY_ID")) or ""

    lat = _dms_to_decimal(
        attrs.get("LAT_DD"), attrs.get("LAT_MM"), attrs.get("LAT_SS")
    )
    lon = _dms_to_decimal(
        attrs.get("LONG_DD"), attrs.get("LONG_MM"), attrs.get("LONG_SS")
    )
    # FL longitudes are west, ensure negative
    if lon is not None and lon > 0:
        lon = -lon

    return Facility(
        source=SOURCE,
        source_id=f"stcm-{fac_id}",
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS1")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=clean(attrs.get("ZIP5")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("FACILITY_TYPE")),
        last_updated=datetime.now(timezone.utc),
    )


def map_pcts_violation_facility(attrs: dict) -> Facility:
    """Create a facility stub from a PCTS Discharge record.

    PCTS discharge records include facility-level info (FACILITY_ID,
    FACILITY_NAME, ADDRESS, CITY, ZIP5). We yield a Facility so that
    discharge-only facilities (not in Registered Tanks) get created.
    Dedup against Registered Tanks happens via FACILITY_ID in the
    source connector.
    """
    fac_id = clean(attrs.get("FACILITY_ID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"stcm-{fac_id}",
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=clean(attrs.get("ZIP5")),
        county=None,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs="PCTS Discharge",
        last_updated=datetime.now(timezone.utc),
    )


def map_dryclean_facility(attrs: dict) -> Facility:
    """Convert Drycleaning Solvent Program Site attributes to a Facility model.

    Key fields: ERIC_ID, NAME, ADDRESS, CITY, COUNTY, PROGRAM, STATUS,
    LAT_DD/LAT_MM/LAT_SS, LONG_DD/LONG_MM/LONG_SS.
    """
    eric_id = clean(attrs.get("ERIC_ID")) or ""

    lat = _dms_to_decimal(
        attrs.get("LAT_DD"), attrs.get("LAT_MM"), attrs.get("LAT_SS")
    )
    lon = _dms_to_decimal(
        attrs.get("LONG_DD"), attrs.get("LONG_MM"), attrs.get("LONG_SS")
    )
    # FL longitudes are west, ensure negative
    if lon is not None and lon > 0:
        lon = -lon

    return Facility(
        source=SOURCE,
        source_id=f"dryclean-{eric_id}",
        name=clean(attrs.get("NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=None,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("PROGRAM")) or "Drycleaning Solvent Cleanup",
        last_updated=datetime.now(timezone.utc),
    )


def map_pcts_violation(attrs: dict) -> Violation:
    """Convert a PCTS Discharge record to a Violation model.

    Key fields: FACILITY_ID, DISCHARGE_ID, DISCHARGE_DATE, DISCHARGE_SCORE,
    ELIGIBILITY, GENERAL_CLEANUP_STATUS, DISCHARGE_CLEANUP_STATUS.
    """
    fac_id = clean(attrs.get("FACILITY_ID")) or ""
    discharge_id = clean(attrs.get("DISCHARGE_ID")) or ""

    desc_parts = []
    eligibility = clean(attrs.get("ELIGIBILITY"))
    general_status = clean(attrs.get("GENERAL_CLEANUP_STATUS"))
    discharge_status = clean(attrs.get("DISCHARGE_CLEANUP_STATUS"))
    score = clean(attrs.get("DISCHARGE_SCORE"))
    if eligibility:
        desc_parts.append(f"Eligibility: {eligibility}")
    if general_status:
        desc_parts.append(f"Cleanup: {general_status}")
    if discharge_status:
        desc_parts.append(f"Discharge cleanup: {discharge_status}")
    if score:
        desc_parts.append(f"Score: {score}")

    return Violation(
        source=SOURCE,
        source_id=f"pcts-{discharge_id}",
        facility_source_id=f"stcm-{fac_id}",
        facility_source=SOURCE,
        violation_type="Petroleum Discharge",
        violation_date=epoch_ms_to_date(attrs.get("DISCHARGE_DATE")),
        statute=None,
        program_area="Storage Tanks",
        severity=None,
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
