"""Map raw Iowa DNR ArcGIS feature data to Pydantic models.

IA DNR data comes from ArcGIS at programs.iowadnr.gov OneStop/QueryEnvFacs MapServer:
  - Layer 0 (Air Facilities): facName, LocAddress, CityName, locZip, countyName,
    Latitude, Longitude, opStatus, ProgType, stfacid
  - Layer 5 (Contaminated Sites): facName, LocAddress, CityName, locZip, countyName,
    Latitude, Longitude, opStatus, ProgType, stfacid
  - Layer 9 (UST): facName, LocAddress, CityName, locZip, countyName,
    Latitude, Longitude, opStatus, stfacid
  - Layer 12 (Wastewater NPDES): facName, LocAddress, CityName, locZip, countyName,
    Latitude, Longitude, opStatus, ProgType, stfacid

Coordinates come from explicit Latitude/Longitude attribute fields.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "ia_dnr"

def map_air_facility(feature: dict) -> Facility:
    """Convert an Air Facilities (Layer 0) feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    fac_id = clean(attrs.get("stfacid")) or clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("Longitude"), zero_as_none=True)

    programs = []
    prog_type = clean(attrs.get("ProgType"))
    if prog_type:
        programs.append(prog_type)
    status = clean(attrs.get("opStatus"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"air-{fac_id}",
        name=clean(attrs.get("facName")) or "Unknown",
        address=clean(attrs.get("LocAddress")),
        city=clean(attrs.get("CityName")),
        state="IA",
        zip_code=clean(attrs.get("locZip")),
        county=clean(attrs.get("countyName")),
        lat=lat,
        lon=lon,
        programs=", ".join(programs) if programs else "Air",
        last_updated=datetime.now(timezone.utc),
    )

def map_contaminated_site(feature: dict) -> Facility:
    """Convert a Contaminated Sites (Layer 5) feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    fac_id = clean(attrs.get("stfacid")) or clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("Longitude"), zero_as_none=True)

    programs = []
    prog_type = clean(attrs.get("ProgType"))
    if prog_type:
        programs.append(prog_type)
    status = clean(attrs.get("opStatus"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"contam-{fac_id}",
        name=clean(attrs.get("facName")) or "Unknown",
        address=clean(attrs.get("LocAddress")),
        city=clean(attrs.get("CityName")),
        state="IA",
        zip_code=clean(attrs.get("locZip")),
        county=clean(attrs.get("countyName")),
        lat=lat,
        lon=lon,
        programs=", ".join(programs) if programs else "Contaminated Site",
        last_updated=datetime.now(timezone.utc),
    )

def map_ust_facility(feature: dict) -> Facility:
    """Convert a UST (Layer 9) feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    fac_id = clean(attrs.get("stfacid")) or clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("Longitude"), zero_as_none=True)

    status = clean(attrs.get("opStatus"))

    return Facility(
        source=SOURCE,
        source_id=f"ust-{fac_id}",
        name=clean(attrs.get("facName")) or "Unknown",
        address=clean(attrs.get("LocAddress")),
        city=clean(attrs.get("CityName")),
        state="IA",
        zip_code=clean(attrs.get("locZip")),
        county=clean(attrs.get("countyName")),
        lat=lat,
        lon=lon,
        programs=f"UST, {status}" if status else "UST",
        last_updated=datetime.now(timezone.utc),
    )

def map_npdes_facility(feature: dict) -> Facility:
    """Convert a Wastewater NPDES (Layer 12) feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    fac_id = clean(attrs.get("stfacid")) or clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("Longitude"), zero_as_none=True)

    programs = []
    prog_type = clean(attrs.get("ProgType"))
    if prog_type:
        programs.append(prog_type)
    status = clean(attrs.get("opStatus"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"npdes-{fac_id}",
        name=clean(attrs.get("facName")) or "Unknown",
        address=clean(attrs.get("LocAddress")),
        city=clean(attrs.get("CityName")),
        state="IA",
        zip_code=clean(attrs.get("locZip")),
        county=clean(attrs.get("countyName")),
        lat=lat,
        lon=lon,
        programs=", ".join(programs) if programs else "NPDES",
        last_updated=datetime.now(timezone.utc),
    )
