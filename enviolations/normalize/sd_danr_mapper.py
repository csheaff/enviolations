"""Map raw South Dakota DANR ArcGIS feature data to Pydantic models.

SD DANR data comes from ArcGIS at arcgis.sd.gov via DENR services.
Three facility datasets:
  - NR40_TankFacilities_Public (MapServer/0): FACILITY_NUMBER, FACILITY_NAME,
    FACILITY_ADDRESS, FACILITY_CITY, FACILITY_COUNTY, FACILITY_SYSTEMTYPE
  - NR34_AirQuality_View (MapServer/0): AirFacilityID, FacilitySiteName,
    LocalityName, Type, Category, LocationAddressCountyCode
  - NR60_SolidWaste (MapServer/0): denrid_no, sw_id, operator, type,
    category, status, city, county, latitude, longitude

Coordinates come from geometry objects (outSR=4326).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "sd_danr"

def map_tank(feature: dict) -> Facility:
    """Convert a Tank Facilities feature to a Facility."""
    attrs = feature.get("attributes", {})

    fac_num = clean(attrs.get("FACILITY_NUMBER")) or ""
    fac_id = attrs.get("FACILITY_ID", "")
    source_id = f"tank-{fac_num}" if fac_num else f"tank-{fac_id}"

    lat, lon = extract_arcgis_coords(feature)

    sys_type = clean(attrs.get("FACILITY_SYSTEMTYPE"))
    active = clean(attrs.get("FACILITY_ACTIVE"))
    programs = []
    if sys_type:
        programs.append(sys_type)
    if active == "TRUE":
        programs.append("Active")
    elif active == "FALSE":
        programs.append("Inactive")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=clean(attrs.get("FACILITY_ADDRESS")),
        city=clean(attrs.get("FACILITY_CITY")),
        state="SD",
        zip_code=clean(attrs.get("FACILITY_ZIPCODE")),
        county=clean(attrs.get("FACILITY_COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Tank",
        last_updated=datetime.now(timezone.utc),
    )

def map_air_facility(feature: dict) -> Facility:
    """Convert an Air Quality facility feature to a Facility."""
    attrs = feature.get("attributes", {})

    air_id = clean(attrs.get("AirFacilityID")) or ""
    source_id = f"air-{air_id}" if air_id else f"air-{attrs.get('ObjectID', '')}"

    lat, lon = extract_arcgis_coords(feature)

    programs = []
    fac_type = clean(attrs.get("Type"))
    if fac_type:
        programs.append(fac_type)
    category = clean(attrs.get("Category"))
    if category:
        programs.append(category)

    # Extract county from code like "SD093"
    county_code = clean(attrs.get("LocationAddressCountyCode"))
    county = None
    if county_code and county_code.startswith("SD"):
        county = county_code

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FacilitySiteName")) or "Unknown",
        address=clean(attrs.get("LocationAddressText")),
        city=clean(attrs.get("LocalityName")),
        state="SD",
        zip_code=clean(attrs.get("LocationZipCode")),
        county=county,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Air Quality",
        last_updated=datetime.now(timezone.utc),
    )

def map_solid_waste(feature: dict) -> Facility:
    """Convert a Solid Waste facility feature to a Facility."""
    attrs = feature.get("attributes", {})

    sw_id = clean(attrs.get("sw_id")) or ""
    denr_id = clean(attrs.get("denrid_no")) or ""
    source_id = f"sw-{sw_id}" if sw_id else f"sw-{denr_id}"

    # Try attribute lat/lon first, fall back to geometry
    lat = parse_float(attrs.get("latitude"), zero_as_none=True)
    lon = parse_float(attrs.get("longitude"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    programs = []
    sw_type = clean(attrs.get("type"))
    if sw_type:
        programs.append(sw_type)
    status = clean(attrs.get("status"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("operator")) or "Unknown",
        address=clean(attrs.get("address")),
        city=clean(attrs.get("city")),
        state="SD",
        zip_code=clean(attrs.get("zip")),
        county=clean(attrs.get("county")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Solid Waste",
        last_updated=datetime.now(timezone.utc),
    )
