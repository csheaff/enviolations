"""Map raw Delaware DNREC ArcGIS feature data to Pydantic models.

DE DNREC data comes from FirstMap ArcGIS Enterprise at
enterprise.firstmap.delaware.gov.

Four facility datasets:
  - UST Facilities (DE_DNREC_Permits layer 0): LOCID, ProgID, Name, PiType
  - Leaking UST (DE_DNREC_Permits layer 1): SiteName, siteid, OpActStatus, substance
  - Air Permitted (DE_DNREC_Facilities layer 1): LOCID, Program_ID, Site_Name, Operating_Status
  - Hazardous Waste Generators (DE_DNREC_Facilities layer 2): ProgID, PiName, RegStatusDesc

Coordinates are requested as WGS84 (outSR=4326) from geometry.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean

SOURCE = "de_dnrec"

def _extract_coords(feature: dict) -> tuple[float | None, float | None]:
    """Extract lat/lon from geometry (WGS84 via outSR=4326)."""
    geom = feature.get("geometry")
    if not geom:
        return None, None
    lon = parse_float(geom.get("x"), zero_as_none=True)
    lat = parse_float(geom.get("y"), zero_as_none=True)
    return lat, lon

def map_ust_facility(feature: dict) -> Facility:
    """Convert a UST Facilities feature to a Facility."""
    attrs = feature.get("attributes", {})

    prog_id = clean(attrs.get("ProgID")) or ""
    locid = clean(attrs.get("LOCID"))
    source_id = f"ust-{prog_id}" if prog_id else f"ust-loc-{locid or attrs.get('OBJECTID', '')}"

    lat, lon = _extract_coords(feature)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("Name")) or "Unknown",
        address=None,
        city=None,
        state="DE",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("PiType")) or "UST",
        last_updated=datetime.now(timezone.utc),
    )

def map_leaking_ust(feature: dict) -> Facility:
    """Convert a Leaking UST feature to a Facility."""
    attrs = feature.get("attributes", {})

    site_id = clean(attrs.get("siteid")) or ""
    lust_id = clean(attrs.get("LustID")) or ""
    source_id = f"lust-{site_id}" if site_id else f"lust-{lust_id or attrs.get('OBJECTID', '')}"

    lat, lon = _extract_coords(feature)

    programs = ["Leaking UST"]
    substance = clean(attrs.get("substance"))
    if substance:
        programs.append(substance)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SiteName")) or "Unknown",
        address=None,
        city=None,
        state="DE",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_air_facility(feature: dict) -> Facility:
    """Convert an Air Permitted Facilities feature to a Facility."""
    attrs = feature.get("attributes", {})

    prog_id = clean(attrs.get("Program_ID")) or ""
    locid = clean(attrs.get("LOCID"))
    source_id = f"air-{prog_id}" if prog_id else f"air-loc-{locid or attrs.get('OBJECTID', '')}"

    lat, lon = _extract_coords(feature)

    programs = ["Air"]
    site_type = clean(attrs.get("Site_Type"))
    if site_type:
        programs.append(site_type)
    status = clean(attrs.get("Operating_Status"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("Site_Name")) or "Unknown",
        address=None,
        city=None,
        state="DE",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_hazwaste_generator(feature: dict) -> Facility:
    """Convert a Hazardous Waste Generators feature to a Facility."""
    attrs = feature.get("attributes", {})

    prog_id = clean(attrs.get("ProgID")) or ""
    pi_id = clean(attrs.get("PiID"))
    source_id = f"hazwaste-{prog_id}" if prog_id else f"hazwaste-{pi_id or attrs.get('OBJECTID', '')}"

    lat, lon = _extract_coords(feature)

    programs = ["Hazardous Waste"]
    status = clean(attrs.get("RegStatusDesc"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("PiName")) or "Unknown",
        address=None,
        city=None,
        state="DE",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )
