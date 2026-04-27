"""Map raw NH DES ArcGIS feature data to Pydantic models.

NH DES data comes from ArcGIS at gis.des.nh.gov/server via DES_Data_Public FeatureServer.
Four facility datasets:
  - Air Facility Systems (Layer 1): AFS, NAME, CITY, NAICS_CODE, SIC_CODE, CLASS, STATUS
  - Hazardous Waste Generators (Layer 7): RCRA_, SITE_NAME, ADDRESS, TOWN, GEN_TYPE, GEN_SIZE
  - Underground Storage Tank Sites (Layer 13): SITE_NO, FACILITY, ADDRESS, TOWN, FACILITY_T
  - Solid Waste Facilities (Layer 12): SWF_LID, SWF_NAME, SWF_TYPE, SWF_STATUS, SWF_CITY

Coordinates come from LATITUDE/LONGITUDE attribute fields.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean

SOURCE = "nh_des"

def map_air_facility(feature: dict) -> Facility:
    """Convert an Air Facility Systems feature to a Facility."""
    attrs = feature.get("attributes", {})

    afs = attrs.get("AFS")
    afs_str = str(int(afs)) if afs and afs == afs else ""
    master_id = attrs.get("MASTERID", "")
    source_id = f"air-{afs_str}" if afs_str else f"air-{master_id}"

    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    programs = []
    cls = clean(attrs.get("CLASS"))
    if cls:
        programs.append(cls)
    status = clean(attrs.get("STATUS"))
    if status:
        programs.append(status)
    ptype = clean(attrs.get("PERMIT_TYPE"))
    if ptype:
        programs.append(ptype)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="NH",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=clean(attrs.get("NAICS_CODE")),
        sic_codes=clean(attrs.get("SIC_CODE")),
        programs=", ".join(programs) if programs else "Air",
        last_updated=datetime.now(timezone.utc),
    )

def map_hazwaste(feature: dict) -> Facility:
    """Convert a Hazardous Waste Generators feature to a Facility."""
    attrs = feature.get("attributes", {})

    rcra_id = clean(attrs.get("RCRA_")) or ""
    master_id = attrs.get("MASTERID", "")
    source_id = f"hazwaste-{rcra_id}" if rcra_id else f"hazwaste-{master_id}"

    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    programs = []
    gen_type = clean(attrs.get("GEN_TYPE"))
    if gen_type:
        programs.append(gen_type)
    gen_size = clean(attrs.get("GEN_SIZE"))
    if gen_size:
        programs.append(gen_size)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SITE_NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("TOWN")),
        state="NH",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Hazardous Waste",
        last_updated=datetime.now(timezone.utc),
    )

def map_ust(feature: dict) -> Facility:
    """Convert an Underground Storage Tank Sites feature to a Facility."""
    attrs = feature.get("attributes", {})

    site_no = attrs.get("SITE_NO")
    site_no_str = str(int(site_no)) if site_no and site_no == site_no else ""
    fac_num = clean(attrs.get("FACILITY_N")) or ""
    master_id = attrs.get("MASTERID", "")
    source_id = f"ust-{fac_num}" if fac_num else (f"ust-{site_no_str}" if site_no_str else f"ust-{master_id}")

    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    programs = []
    fac_type = clean(attrs.get("FACILITY_T"))
    if fac_type:
        programs.append(fac_type)
    gis_type = clean(attrs.get("GIS_TYPE"))
    if gis_type:
        programs.append(gis_type)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FACILITY")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("TOWN")),
        state="NH",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "UST",
        last_updated=datetime.now(timezone.utc),
    )

def map_solid_waste(feature: dict) -> Facility:
    """Convert a Solid Waste Facilities feature to a Facility."""
    attrs = feature.get("attributes", {})

    swf_lid = attrs.get("SWF_LID")
    swf_str = str(int(swf_lid)) if swf_lid and swf_lid == swf_lid else ""
    master_id = attrs.get("MASTERID", "")
    source_id = f"sw-{swf_str}" if swf_str else f"sw-{master_id}"

    lat = parse_float(attrs.get("SWF_LAT"), zero_as_none=True)
    lon = parse_float(attrs.get("SWF_LONG"), zero_as_none=True)

    programs = []
    swf_type = clean(attrs.get("SWF_TYPE"))
    if swf_type:
        programs.append(swf_type)
    swf_status = clean(attrs.get("SWF_STATUS"))
    if swf_status:
        programs.append(swf_status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SWF_NAME")) or "Unknown",
        address=clean(attrs.get("SWF_ADD_1")),
        city=clean(attrs.get("SWF_CITY")),
        state="NH",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Solid Waste",
        last_updated=datetime.now(timezone.utc),
    )
