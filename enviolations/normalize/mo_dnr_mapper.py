"""Map raw Missouri DNR ArcGIS feature data to Pydantic models.

MO DNR data comes from ArcGIS MapServer layers at gis.dnr.mo.gov.
Five facility datasets:
  - Hazardous Waste Generators (MapServer/0) → Facility (EPA_ID key)
  - Air Facility Locations (MapServer/0) → Facility (SITE_ID key)
  - Public Drinking Water Systems (MapServer/0) → Facility (IPWS key)
  - E-Start Cleanup Sites (MapServer/0) → Facility (OBJECTID_1 key)
  - UST Facilities (MapServer/3) → Facility (FACILITYID key)

All coordinates come from geometry objects (outSR=4326), not UTM attributes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords

SOURCE = "mo_dnr"


def map_haz_waste(feature: dict) -> Facility:
    """Convert a Hazardous Waste Generators feature to a Facility model.

    Key fields: EPA_ID, MISSOURI_I, FACILITY_N, FACILITYAD, FACILITYCI,
    FACILITYST, FACILITYZI, COUNTYNAME, FACILITY_S, REGION.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    epa_id = clean(attrs.get("EPA_ID")) or ""
    status = clean(attrs.get("FACILITY_S"))

    return Facility(
        source=SOURCE,
        source_id=f"hw-{epa_id}",
        name=clean(attrs.get("FACILITY_N")) or "Unknown",
        address=clean(attrs.get("FACILITYAD")),
        city=clean(attrs.get("FACILITYCI")),
        state=clean(attrs.get("FACILITYST")) or "MO",
        zip_code=clean(attrs.get("FACILITYZI")),
        county=clean(attrs.get("COUNTYNAME")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=status or "Hazardous Waste",
        last_updated=datetime.now(timezone.utc),
    )


def map_air_facility(feature: dict) -> Facility:
    """Convert an Air Facility Locations feature to a Facility model.

    Key fields: SITE_ID, PLANT_ID, PLANT_NAME, PADDRESS, PLOCALNAME,
    PSTATEABBR, PZIPCODE, COUNTYFIPS, SIC_CODE, STATUS, TYPE_OP.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    site_id = attrs.get("SITE_ID")
    source_id = f"air-{int(site_id)}" if site_id is not None else ""

    status = clean(attrs.get("STATUS"))
    type_op = clean(attrs.get("TYPE_OP"))
    programs = []
    if type_op:
        programs.append(type_op)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("PLANT_NAME")) or "Unknown",
        address=clean(attrs.get("PADDRESS")),
        city=clean(attrs.get("PLOCALNAME")),
        state=clean(attrs.get("PSTATEABBR")) or "MO",
        zip_code=clean(attrs.get("PZIPCODE")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=clean(attrs.get("SIC_CODE")),
        programs=", ".join(programs) if programs else "Air",
        last_updated=datetime.now(timezone.utc),
    )


def map_drinking_water(feature: dict) -> Facility:
    """Convert a Public Drinking Water Systems feature to a Facility model.

    Key fields: IPWS, SDWISNAME, COUNTY, STATUS, SRC_TYPE, FED_TYPE, FACTYPE.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    ipws = clean(attrs.get("IPWS")) or ""

    fed_type = clean(attrs.get("FED_TYPE"))
    src_type = clean(attrs.get("SRC_TYPE"))
    factype = clean(attrs.get("FACTYPE"))
    programs = []
    if fed_type:
        programs.append(fed_type)
    if src_type:
        programs.append(src_type)
    if factype:
        programs.append(factype)

    return Facility(
        source=SOURCE,
        source_id=f"pws-{ipws}",
        name=clean(attrs.get("SDWISNAME")) or "Unknown",
        address=None,
        city=None,
        state="MO",
        zip_code=None,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Public Water",
        last_updated=datetime.now(timezone.utc),
    )


def map_cleanup_site(feature: dict) -> Facility:
    """Convert an E-Start Cleanup Sites feature to a Facility model.

    Key fields: OBJECTID_1, SITENAME, ADDRESS, CITY, ZIP, COUNTY,
    SITESTAT, DNRPROGRAM, FEDERALID, SMARSID.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    oid = attrs.get("OBJECTID_1")
    source_id = f"cleanup-{int(oid)}" if oid is not None else ""

    status = clean(attrs.get("SITESTAT"))
    program = clean(attrs.get("DNRPROGRAM"))
    programs = []
    if program:
        programs.append(program)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SITENAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="MO",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Cleanup",
        last_updated=datetime.now(timezone.utc),
    )


def map_ust(feature: dict) -> Facility:
    """Convert a UST (Underground Storage Tanks) feature to a Facility model.

    Key fields: FACILITYID, FACNAME, ADDRESS, CITY, ZIP, COUNTY,
    FACSTAT, FACTYP, SITEOWN, FEDERALID.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    fac_id = clean(attrs.get("FACILITYID")) or ""

    factyp = clean(attrs.get("FACTYP"))
    facstat = clean(attrs.get("FACSTAT"))
    programs = []
    if factyp:
        programs.append(factyp)
    if facstat:
        programs.append(facstat)

    return Facility(
        source=SOURCE,
        source_id=f"ust-{fac_id}",
        name=clean(attrs.get("FACNAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="MO",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "UST",
        last_updated=datetime.now(timezone.utc),
    )
