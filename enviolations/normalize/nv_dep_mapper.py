"""Map raw Nevada DEP ArcGIS feature data to Pydantic models.

NV DEP data comes from ArcGIS at ndep-emap.ndep.nv.gov:
  - eMap_BCA (Corrective Action): SiteNumber, FacilityName, FACILITYADDRESS,
    City, FACILITYZIP, County, Lat_Decdeg, Long_Decdeg, PROGRAM, CONTAMINANT
  - eMap_Air (Air Facilities): AIMS_ID, FacilityName, Address, City, County,
    Latitude, Longitude
  - eMap_BWPC (Water Permits): PermitNumber, FacilityName, Address, City,
    County, Latitude, Longitude

Coordinates come from explicit lat/lon attribute fields or geometry.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float, extract_arcgis_coords
SOURCE = "nv_dep"

def _clean(val) -> str | None:
    return clean(val, sentinel=True, normalize_ws=True)

def map_bca_site(feature: dict, status: str = "") -> Facility:
    """Convert a BCA (Bureau of Corrective Actions) site feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    site_num = _clean(attrs.get("SiteNumber")) or _clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("Lat_Decdeg"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("Long_Decdeg"), zero_as_none=True)

    programs = []
    program = _clean(attrs.get("PROGRAM"))
    if program:
        programs.append(program)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"bca-{site_num}",
        name=_clean(attrs.get("FacilityName")) or "Unknown",
        address=_clean(attrs.get("FACILITYADDRESS")),
        city=_clean(attrs.get("City")),
        state="NV",
        zip_code=_clean(attrs.get("FACILITYZIP")),
        county=_clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        programs=", ".join(programs) if programs else "Corrective Action",
        last_updated=datetime.now(timezone.utc),
    )

def map_air_facility(feature: dict) -> Facility:
    """Convert an Air Facility feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    aims_id = _clean(attrs.get("AIMS_ID")) or _clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("Longitude"), zero_as_none=True)

    return Facility(
        source=SOURCE,
        source_id=f"air-{aims_id}",
        name=_clean(attrs.get("FacilityName")) or "Unknown",
        address=_clean(attrs.get("Address")),
        city=_clean(attrs.get("City")),
        state="NV",
        county=_clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        programs="Air Quality",
        last_updated=datetime.now(timezone.utc),
    )

def map_water_permit(feature: dict) -> Facility:
    """Convert a Water Pollution Control permit feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    permit_num = _clean(attrs.get("PermitNumber")) or _clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("Longitude"), zero_as_none=True)

    return Facility(
        source=SOURCE,
        source_id=f"water-{permit_num}",
        name=_clean(attrs.get("FacilityName")) or "Unknown",
        address=_clean(attrs.get("Address")),
        city=_clean(attrs.get("City")),
        state="NV",
        county=_clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        programs="Water Pollution Control",
        last_updated=datetime.now(timezone.utc),
    )
