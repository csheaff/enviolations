"""Map raw Mississippi MDEQ ArcGIS feature data to Pydantic models.

MS MDEQ data comes from the MUSTER ArcGIS MapServer at
opcgis.deq.state.ms.us.

Two facility datasets:
  - All UST (layer 10): FACILITY_ID, FACILITY_NAME, ADDRESS, CITY, COUNTY,
    FACILITY_ZIP, STATUS, LATITUDE, LONGITUDE, RELEASE_STATUS, TOTAL_TANKS
  - GARD CERCLA (layer 12): Superfund/uncontrolled contamination sites
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean

SOURCE = "ms_mdeq"

def map_ust_facility(feature: dict) -> Facility:
    """Convert a MUSTER All UST feature to a Facility."""
    attrs = feature.get("attributes", {})

    fac_id = clean(attrs.get("FACILITY_ID"))
    source_id = f"ust-{fac_id}" if fac_id else f"ust-{attrs.get('OBJECTID', '')}"

    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    programs = ["UST"]
    status = clean(attrs.get("STATUS"))
    if status:
        programs.append(status)
    release = clean(attrs.get("RELEASE_STATUS"))
    if release:
        programs.append(release)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="MS",
        zip_code=clean(attrs.get("FACILITY_ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_cercla_site(feature: dict) -> Facility:
    """Convert a GARD CERCLA site feature to a Facility."""
    attrs = feature.get("attributes", {})

    # CERCLA sites may have different field names; try common patterns
    site_id = clean(attrs.get("SITE_ID")) or clean(attrs.get("OBJECTID"))
    source_id = f"cercla-{site_id}" if site_id else f"cercla-{attrs.get('OBJECTID', '')}"

    # Try to get coordinates from attributes or geometry
    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True) or parse_float(attrs.get("LAT"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True) or parse_float(attrs.get("LON"), zero_as_none=True)
    if not lat or not lon:
        geom = feature.get("geometry")
        if geom:
            lon = parse_float(geom.get("x"), zero_as_none=True)
            lat = parse_float(geom.get("y"), zero_as_none=True)

    name = (clean(attrs.get("SITE_NAME"))
            or clean(attrs.get("NAME"))
            or clean(attrs.get("FACILITY_NAME"))
            or "Unknown")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=name,
        address=clean(attrs.get("ADDRESS")) or clean(attrs.get("STREET_ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="MS",
        zip_code=clean(attrs.get("ZIP")) or clean(attrs.get("ZIP_CODE")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="CERCLA",
        last_updated=datetime.now(timezone.utc),
    )
