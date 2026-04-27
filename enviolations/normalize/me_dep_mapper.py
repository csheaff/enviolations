"""Map raw Maine DEP ArcGIS feature data to Pydantic models.

Maine DEP data comes from ArcGIS at gis.maine.gov across three services:
  - all_registered_tanks (MapServer/0): REGISTRATION_NUMBER, FACILITY_NAME,
    FACILITY_STREET_ADDRESS, FACILITY_LOCATION_TOWN, TANK_STATUS, LATITUDE, LONGITUDE
  - Remediation_Sites (MapServer/0): SITENUMBER, SITENAME, STREET_ADDRESS,
    MCDTOWN, COUNTY, PROGRAM, STATUS, LATDD, LONGDD
  - MainePollutantDischargeEliminationSystem (MapServer/0): NEPDES_LICENSE_ID,
    SITE_NAME, ADDRESS_1, CITY, COUNTY_NAME, STATUS, LICENSED_FLOW

Coordinates come from explicit lat/lon attribute fields.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean

SOURCE = "me_dep"

def map_tank(feature: dict) -> Facility:
    """Convert a registered tank feature to a Facility."""
    attrs = feature.get("attributes", {})

    reg_num = attrs.get("REGISTRATION_NUMBER")
    tank_num = attrs.get("TANK_NUMBER")
    source_id = f"tank-{reg_num}" if reg_num else f"tank-{attrs.get('OBJECTID', '')}"

    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    status = clean(attrs.get("TANK_STATUS"))
    above_below = clean(attrs.get("TANK_ABOVE_BELOW"))
    programs = []
    if above_below == "B":
        programs.append("UST")
    elif above_below == "A":
        programs.append("AST")
    else:
        programs.append("Tank")
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=clean(attrs.get("FACILITY_STREET_ADDRESS")),
        city=clean(attrs.get("FACILITY_LOCATION_TOWN")),
        state="ME",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Tank",
        last_updated=datetime.now(timezone.utc),
    )

def map_remediation(feature: dict) -> Facility:
    """Convert a Remediation Sites feature to a Facility."""
    attrs = feature.get("attributes", {})

    site_num = clean(attrs.get("SITENUMBER")) or ""
    source_id = f"rem-{site_num}" if site_num else f"rem-{attrs.get('OBJECTID', '')}"

    lat = parse_float(attrs.get("LATDD"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGDD"), zero_as_none=True)

    program = clean(attrs.get("PROGRAM"))
    status = clean(attrs.get("STATUS"))
    programs = []
    if program:
        programs.append(program)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SITENAME")) or "Unknown",
        address=clean(attrs.get("STREET_ADDRESS")),
        city=clean(attrs.get("MCDTOWN")),
        state="ME",
        zip_code=None,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Remediation",
        last_updated=datetime.now(timezone.utc),
    )

def map_mepdes(feature: dict) -> Facility:
    """Convert a MEPDES water discharge facility to a Facility."""
    attrs = feature.get("attributes", {})

    license_id = clean(attrs.get("NEPDES_LICENSE_ID")) or ""
    me_license = clean(attrs.get("MAINE_LICENSE_ID")) or ""
    source_id = f"mepdes-{license_id}" if license_id else f"mepdes-{me_license}"

    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    programs = []
    rating = clean(attrs.get("FACILITY_RATING_CODE"))
    if rating:
        programs.append(rating)
    status = clean(attrs.get("STATUS"))
    if status:
        programs.append(status)
    if clean(attrs.get("MUNICIPAL_WWTF")) == "YES":
        programs.append("Municipal WWTF")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SITE_NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS_1")),
        city=clean(attrs.get("CITY")),
        state="ME",
        zip_code=None,
        county=clean(attrs.get("COUNTY_NAME")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "MEPDES",
        last_updated=datetime.now(timezone.utc),
    )
