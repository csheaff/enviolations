"""Map raw North Dakota DEQ ArcGIS feature data to Pydantic models.

ND DEQ data comes from ArcGIS at ndgishub.nd.gov via All_Locations MapServer.
Two facility datasets:
  - Landfills (Layer 12): FacilityID, FacilityName, OwnerName, WasteType,
    FacilityCity, County, Latitude, Longitude, PermitNumber
  - Abandoned Mines (Layer 6): NAME_SHORT, MINE_TYPE, COUNTY

Coordinates come from explicit lat/lon attributes or geometry.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "nd_deq"

def map_landfill(feature: dict) -> Facility:
    """Convert a Landfills feature to a Facility."""
    attrs = feature.get("attributes", {})

    fac_id = attrs.get("FacilityID")
    fac_id_str = str(int(fac_id)) if fac_id and fac_id == fac_id else ""
    source_id = f"landfill-{fac_id_str}" if fac_id_str else f"landfill-{attrs.get('OBJECTID', '')}"

    lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    lon = parse_float(attrs.get("Longitude"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    programs = []
    waste_type = clean(attrs.get("WasteType"))
    if waste_type:
        programs.append(waste_type)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FacilityName")) or "Unknown",
        address=clean(attrs.get("FacilityAddress")),
        city=clean(attrs.get("FacilityCity")),
        state="ND",
        zip_code=clean(attrs.get("Zip")),
        county=clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Landfill",
        last_updated=datetime.now(timezone.utc),
    )

def map_abandoned_mine(feature: dict) -> Facility:
    """Convert an Abandoned Mines feature to a Facility."""
    attrs = feature.get("attributes", {})

    obj_id = attrs.get("OBJECTID", "")
    source_id = f"mine-{obj_id}"

    lat, lon = extract_arcgis_coords(feature)

    programs = []
    mine_type = clean(attrs.get("MINE_TYPE"))
    if mine_type and mine_type != "UNKNOWN":
        programs.append(mine_type)
    programs.append("Abandoned Mine")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("NAME_SHORT")) or clean(attrs.get("NAME_LONG")) or "Unknown",
        address=None,
        city=None,
        state="ND",
        zip_code=None,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Abandoned Mine",
        last_updated=datetime.now(timezone.utc),
    )
