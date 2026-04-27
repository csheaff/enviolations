"""Map raw Hawaii DOH ArcGIS feature data to Pydantic models.

Hawaii DOH data comes from geodata.hawaii.gov MapServer services.
Two facility datasets:
  - Brightfields Initiative Data (LandUseLandCover/13): DOH_BROWNFIELDS_NUMBER,
    HANDLER_ID_NUMBER, HEER_FACILITY_NUMBER, HEER_PROGRAM_NAME, SITE_NAME,
    ISLAND, TMK
  - Regulated Dams (Infrastructure/10): dam_name, county, owner_name,
    dam_type, purposes, latitude, longitude, downstream_hazard_potential
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "hi_doh"

def map_brightfield(feature: dict) -> Facility:
    """Convert a Brightfields Initiative feature to a Facility.

    Key fields: DOH_BROWNFIELDS_NUMBER, HANDLER_ID_NUMBER, HEER_FACILITY_NUMBER,
    HEER_PROGRAM_NAME, SITE_NAME, ISLAND, TMK, ADDRESS.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    # Use DOH brownfields number or HEER facility number as source_id
    bf_num = clean(attrs.get("DOH_BROWNFIELDS_NUMBER"))
    heer_num = clean(attrs.get("HEER_FACILITY_NUMBER"))
    handler_id = clean(attrs.get("HANDLER_ID_NUMBER"))
    obj_id = attrs.get("OBJECTID", "")

    if bf_num:
        source_id = f"bf-{bf_num}"
    elif heer_num:
        source_id = f"heer-{heer_num}"
    elif handler_id:
        source_id = f"handler-{handler_id}"
    else:
        source_id = f"bf-{obj_id}"

    programs = []
    heer_program = clean(attrs.get("HEER_PROGRAM_NAME"))
    if heer_program:
        programs.append(heer_program)
    programs.append("Brightfield/Brownfield")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SITE_NAME")) or clean(attrs.get("NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=None,
        state="HI",
        zip_code=None,
        county=clean(attrs.get("ISLAND")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_dam(feature: dict) -> Facility:
    """Convert a Regulated Dams feature to a Facility.

    Key fields: dam_name, county, owner_name, dam_type, purposes,
    latitude, longitude, downstream_hazard_potential, nid_id,
    condition_assessment.
    """
    attrs = feature.get("attributes", {})

    nid_id = clean(attrs.get("nid_id"))
    record_id = attrs.get("record_id")
    obj_id = attrs.get("objectid", "")

    if nid_id:
        source_id = f"dam-{nid_id}"
    elif record_id:
        source_id = f"dam-{record_id}"
    else:
        source_id = f"dam-{obj_id}"

    lat = parse_float(attrs.get("latitude"), zero_as_none=True)
    lon = parse_float(attrs.get("longitude"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    programs = ["Dam"]
    hazard = clean(attrs.get("downstream_hazard_potential"))
    condition = clean(attrs.get("condition_assessment"))
    if hazard:
        programs.append(f"Hazard: {hazard}")
    if condition:
        programs.append(f"Condition: {condition}")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("dam_name")) or "Unknown",
        address=None,
        city=clean(attrs.get("nearest_downstream_city_town")),
        state="HI",
        zip_code=None,
        county=clean(attrs.get("county")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )
