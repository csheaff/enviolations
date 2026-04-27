"""Map raw DC DOEE ArcGIS feature data to Pydantic models.

DC DOEE data comes from maps2.dcgis.dc.gov via the
Facility_and_Structure_WebMercator MapServer.
Three facility datasets:
  - UST (Layer 11): FAC_ID, FAC_NAME, ADDRESS, ZIPCODE, WARD, TANKSTATUS,
    REGISTERED_TANKS, ACTIVE_TANKS, LATITUDE, LONGITUDE
  - LUST (Layer 16): FACILITY_ID, CASE_NUM, COMPANY_NAME, SITE_ADDRESS,
    STATUS, PRODUCT, RECEPTOR, MEDIA_OF_CONTAMINATION, LATITUDE, LONGITUDE
  - AST (Layer 10): FAC_ID, FAC_NAME, ADDRESS, ZIPCODE, WARD, TANKSTATUS,
    LATITUDE, LONGITUDE
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "dc_doee"

def map_ust(feature: dict) -> Facility:
    """Convert an Underground Storage Tanks feature to a Facility.

    Key fields: FAC_ID, FAC_NAME, NAME, ADDRESS, ZIPCODE, WARD,
    TANKSTATUS, REGISTERED_TANKS, ACTIVE_TANKS, LATITUDE, LONGITUDE.
    """
    attrs = feature.get("attributes", {})

    fac_id = clean(attrs.get("FAC_ID")) or str(attrs.get("OBJECTID", ""))
    source_id = f"ust-{fac_id}"

    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    programs = ["UST"]
    status = clean(attrs.get("TANKSTATUS"))
    if status:
        programs.append(status)

    name = clean(attrs.get("FAC_NAME")) or clean(attrs.get("NAME")) or "Unknown"

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=name,
        address=clean(attrs.get("ADDRESS")),
        city="Washington",
        state="DC",
        zip_code=clean(attrs.get("ZIPCODE")),
        county=clean(attrs.get("WARD")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_lust(feature: dict) -> Facility:
    """Convert a Leaking Underground Storage Tanks feature to a Facility.

    Key fields: FACILITY_ID, CASE_NUM, COMPANY_NAME, FACILITY_TYPE,
    SITE_ADDRESS, STATUS, PRODUCT, RECEPTOR, MEDIA_OF_CONTAMINATION,
    LATITUDE, LONGITUDE.
    """
    attrs = feature.get("attributes", {})

    fac_id = clean(attrs.get("FACILITY_ID")) or clean(attrs.get("CASE_NUM")) or str(attrs.get("OBJECTID", ""))
    source_id = f"lust-{fac_id}"

    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    programs = ["LUST"]
    status = clean(attrs.get("STATUS"))
    product = clean(attrs.get("PRODUCT"))
    if status:
        programs.append(status)
    if product:
        programs.append(product)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("COMPANY_NAME")) or "Unknown",
        address=clean(attrs.get("SITE_ADDRESS")),
        city="Washington",
        state="DC",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_ast(feature: dict) -> Facility:
    """Convert an Above Ground Storage Tanks feature to a Facility.

    Key fields: FAC_ID, FAC_NAME, NAME, ADDRESS, ZIPCODE, WARD,
    TANKSTATUS, LATITUDE, LONGITUDE.
    """
    attrs = feature.get("attributes", {})

    fac_id = clean(attrs.get("FAC_ID")) or str(attrs.get("OBJECTID", ""))
    source_id = f"ast-{fac_id}"

    lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    programs = ["AST"]
    status = clean(attrs.get("TANKSTATUS"))
    if status:
        programs.append(status)

    name = clean(attrs.get("FAC_NAME")) or clean(attrs.get("NAME")) or "Unknown"

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=name,
        address=clean(attrs.get("ADDRESS")),
        city="Washington",
        state="DC",
        zip_code=clean(attrs.get("ZIPCODE")),
        county=clean(attrs.get("WARD")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )
