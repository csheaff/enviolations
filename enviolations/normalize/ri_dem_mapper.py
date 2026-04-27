"""Map raw Rhode Island DEM ArcGIS feature data to Pydantic models.

RI DEM data comes from RIGIS ArcGIS services.
Two facility datasets:
  - Active Solid Waste Facility Sites: OBJECTID, Facility, Map_Catego,
    Regulation, Match_addr, Fac_Type, Own_Type, Oper_Type
  - Brownfield Sites (Regulated Facilities layer 23): OBJECTID, Siterem_ID,
    Project, Address, Muni, Status, PrjCode, Lat, Lon, Brownfield
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "ri_dem"

def _parse_match_addr(match_addr: str | None) -> tuple[str | None, str | None, str | None]:
    """Parse 'ADDRESS, CITY, ZIP' from Match_addr field."""
    if not match_addr:
        return None, None, None
    parts = [p.strip() for p in match_addr.split(",")]
    address = parts[0] if len(parts) >= 1 else None
    city = parts[1] if len(parts) >= 2 else None
    zip_code = parts[2] if len(parts) >= 3 else None
    return clean(address), clean(city), clean(zip_code)

def map_solid_waste(feature: dict) -> Facility:
    """Convert an Active Solid Waste Facility Sites feature to a Facility.

    Key fields: OBJECTID, Facility, Map_Catego, Regulation, Match_addr,
    Fac_Type, Own_Type, Oper_Type.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    obj_id = attrs.get("OBJECTID", "")
    name = clean(attrs.get("Facility"))
    source_id = f"sw-{obj_id}"

    address, city, zip_code = _parse_match_addr(attrs.get("Match_addr"))

    programs = []
    category = clean(attrs.get("Map_Catego"))
    regulation = clean(attrs.get("Regulation"))
    if category:
        programs.append(category)
    if regulation:
        programs.append(regulation)
    if not programs:
        programs.append("Solid Waste")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=name or "Unknown",
        address=address,
        city=city,
        state="RI",
        zip_code=zip_code,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_brownfield(feature: dict) -> Facility:
    """Convert a Brownfield Sites feature to a Facility.

    Key fields: OBJECTID, Siterem_ID, Project, Address, Muni, Status,
    PrjCode, Lat, Lon, Brownfield.
    """
    attrs = feature.get("attributes", {})

    # Prefer explicit Lat/Lon fields; fall back to geometry
    lat = parse_float(attrs.get("Lat"), zero_as_none=True)
    lon = parse_float(attrs.get("Lon"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    # Use Siterem_ID as stable source_id (e.g. "SR-28-1131"), fall back to OBJECTID
    siterem_id = clean(attrs.get("Siterem_ID"))
    obj_id = attrs.get("OBJECTID", "")
    source_id = f"bf-{siterem_id}" if siterem_id else f"bf-{obj_id}"

    programs = ["Brownfield"]
    status = clean(attrs.get("Status"))
    prj_code = clean(attrs.get("PrjCode"))
    if status:
        programs.append(status)
    if prj_code:
        programs.append(prj_code)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("Project")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("Muni")),
        state="RI",
        zip_code=None,
        county=clean(attrs.get("Muni")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )
