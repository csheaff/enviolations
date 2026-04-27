"""Map raw FL DEP Solid Waste and ICR ArcGIS features to Pydantic models.

FL DEP Division of Waste Management data from ArcGIS REST API at
ca.dep.state.fl.us. Two datasets:
  - DWM_WASTE_ICR_BACKG/MapServer/1: Solid Waste Facilities → Facility
  - DWM_WASTE_ICR_BACKG/MapServer/12: Institutional Controls Registry → Facility

Solid Waste facilities use DMS coordinates in attributes (LAT_DD/MM/SS).
ICR records may use ArcGIS geometry or DMS; we try geometry first, then DMS.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords, parse_float

SOURCE = "fl_dep_waste"


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


def map_solid_waste_facility(attrs: dict) -> Facility:
    """Convert a Solid Waste facility feature attributes to a Facility model.

    Key fields: FACILITY_ID, FACILITY_NAME, ADDRESS, CITY, ZIP5, COUNTY,
    FACILITY_TYPE, FACILITY_STATUS, CLASS, OWNERSHIP,
    LAT_DD/LAT_MM/LAT_SS, LONG_DD/LONG_MM/LONG_SS (DMS coordinates).
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

    # Use FACILITY_TYPE for programs (e.g., "Landfill", "Transfer Station")
    programs = clean(attrs.get("FACILITY_TYPE")) or "Solid Waste"

    return Facility(
        source=SOURCE,
        source_id=f"swaste-{fac_id}",
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=clean(attrs.get("ZIP5")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_icr_facility(feature: dict) -> Facility:
    """Convert an Institutional Controls Registry feature to a Facility model.

    Accepts the full ArcGIS feature dict (with geometry) for coordinates.
    Tries geometry-based coords first, then falls back to DMS fields.

    Key fields: PRIMARY_FACILITY_ID (or PRIMARY_FA with truncation),
    PRIMARY_FACILITY_NAME, ADDRESS, CITY, ZIP5, COUNTY,
    BOUNDARY_CONTAMINATIONS, BOUNDARY_RESTRICTIONS.
    """
    attrs = feature.get("attributes", {})

    # Try ArcGIS geometry first
    lat, lon = extract_arcgis_coords(feature)

    # Fall back to DMS if geometry didn't provide coords
    if lat is None and lon is None:
        lat = _dms_to_decimal(
            attrs.get("LAT_DD"), attrs.get("LAT_MM"), attrs.get("LAT_SS")
        )
        lon = _dms_to_decimal(
            attrs.get("LONG_DD"), attrs.get("LONG_MM"), attrs.get("LONG_SS")
        )
        # FL longitudes are west, ensure negative
        if lon is not None and lon > 0:
            lon = -lon

    # Handle field name truncation: PRIMARY_FACILITY_ID may appear as PRIMARY_FA
    fac_id = (
        clean(attrs.get("PRIMARY_FACILITY_ID"))
        or clean(attrs.get("PRIMARY_FA"))
        or ""
    )

    name = (
        clean(attrs.get("PRIMARY_FACILITY_NAME"))
        or clean(attrs.get("PRIMARY_FA_1"))
        or "Unknown"
    )

    # Build programs string with contamination/restriction info
    programs_parts = ["Institutional Control"]
    contaminations = clean(attrs.get("BOUNDARY_CONTAMINATIONS"))
    restrictions = clean(attrs.get("BOUNDARY_RESTRICTIONS"))
    if contaminations:
        programs_parts.append(contaminations)
    if restrictions:
        programs_parts.append(restrictions)
    programs = ", ".join(programs_parts)

    return Facility(
        source=SOURCE,
        source_id=f"icr-{fac_id}",
        name=name,
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=clean(attrs.get("ZIP5")) or clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )
