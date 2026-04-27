"""Map raw Montana DEQ ArcGIS feature data to Pydantic models.

MT DEQ data comes from ArcGIS at gis.mtdeq.us/hosting via Hosted FeatureServer.
Three publicly accessible facility datasets:
  - Montana_UST_Facilities_ (FeatureServer/0): fac_code, fac_name, fac_addr,
    fac_city, fac_zip, fac_tank_cnt
  - Montana_Solid_Waste_Facilities (FeatureServer/0): fac_code, fac_name,
    fac_addr, fac_city, license_type, license_status, license_num
  - Montana_Opencut_Mining_Sites (FeatureServer/0): opencutnumber, sitename,
    operatorname, county, sitestatus

Coordinates come from geometry objects (outSR=4326).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "mt_deq"

def map_ust(feature: dict) -> Facility:
    """Convert a UST Facilities feature to a Facility."""
    attrs = feature.get("attributes", {})

    fac_code = attrs.get("fac_code")
    fac_code_str = str(int(fac_code)) if fac_code and fac_code == fac_code else ""
    source_id = f"ust-{fac_code_str}" if fac_code_str else f"ust-{attrs.get('objectid', '')}"

    lat, lon = extract_arcgis_coords(feature)

    tank_cnt = attrs.get("fac_tank_cnt")
    programs = ["UST"]
    if tank_cnt:
        programs.append(f"{tank_cnt} tanks")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("fac_name")) or "Unknown",
        address=clean(attrs.get("fac_addr")),
        city=clean(attrs.get("fac_city")),
        state="MT",
        zip_code=clean(attrs.get("fac_zip")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_solid_waste(feature: dict) -> Facility:
    """Convert a Solid Waste Facilities feature to a Facility."""
    attrs = feature.get("attributes", {})

    fac_code = attrs.get("fac_code")
    fac_code_str = str(int(fac_code)) if fac_code and fac_code == fac_code else ""
    source_id = f"sw-{fac_code_str}" if fac_code_str else f"sw-{attrs.get('objectid', '')}"

    lat, lon = extract_arcgis_coords(feature)

    programs = []
    lic_type = clean(attrs.get("license_type"))
    if lic_type:
        programs.append(lic_type)
    lic_status = clean(attrs.get("license_status"))
    if lic_status:
        programs.append(lic_status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("fac_name")) or "Unknown",
        address=clean(attrs.get("fac_addr")),
        city=clean(attrs.get("fac_city")),
        state="MT",
        zip_code=clean(attrs.get("fac_zip")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Solid Waste",
        last_updated=datetime.now(timezone.utc),
    )

def map_opencut_mine(feature: dict) -> Facility:
    """Convert an Opencut Mining Sites feature to a Facility."""
    attrs = feature.get("attributes", {})

    oc_num = attrs.get("opencutnumber")
    oc_str = str(int(oc_num)) if oc_num and oc_num == oc_num else ""
    source_id = f"mine-{oc_str}" if oc_str else f"mine-{attrs.get('objectid', '')}"

    lat, lon = extract_arcgis_coords(feature)

    programs = ["Opencut Mining"]
    status = clean(attrs.get("sitestatus"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("sitename")) or "Unknown",
        address=None,
        city=None,
        state="MT",
        zip_code=None,
        county=clean(attrs.get("county")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )
