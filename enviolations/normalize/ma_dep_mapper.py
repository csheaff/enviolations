"""Map raw MA DEP ArcGIS feature attributes to Pydantic models.

MA DEP data comes from MassGIS ArcGIS FeatureServer endpoints. Three datasets:
  - DEP Air Facilities → Facility (1,447 records)
  - Solid Waste Disposal Land → Facility (612 records)
  - Public Water Supply Sources → Facility (3,990 records)

MA DEP does not expose a structured violation dataset via ArcGIS;
violations for Massachusetts come from federal EPA ECHO (already ingested).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float

SOURCE = "ma_dep"


def map_air_facility(attrs: dict) -> Facility:
    """Convert a DEP Air Facility feature to a Facility model.

    Key fields: FAC_ID, FAC_NAME, STREET_ADDRESS, CITY, ZIP_CODE,
    LATITUDE, LONGITUDE, FACILITY_CATEGORY.
    """
    fac_id = clean(attrs.get("FAC_ID")) or clean(attrs.get("OBJECTID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"air-{fac_id}",
        name=clean(attrs.get("FAC_NAME")) or "Unknown",
        address=clean(attrs.get("STREET_ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="MA",
        zip_code=clean(attrs.get("ZIP_CODE")),
        county=None,
        lat=parse_float(attrs.get("LATITUDE")),
        lon=parse_float(attrs.get("LONGITUDE")),
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("FACILITY_CATEGORY")),
        last_updated=datetime.now(timezone.utc),
    )


def map_sw_disposal(attrs: dict) -> Facility:
    """Convert a Solid Waste Disposal Land feature to a Facility model.

    Key fields: SITE_NAME, LOCATION, TOWN, DEP_ID, SW_CATEGORY.
    """
    dep_id = clean(attrs.get("DEP_ID")) or clean(attrs.get("OBJECTID")) or ""

    programs_parts = []
    cat = clean(attrs.get("SW_CATEGORY"))
    if cat:
        programs_parts.append(cat)
    status = clean(attrs.get("STATUS"))
    if status:
        programs_parts.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"sw-{dep_id}",
        name=clean(attrs.get("SITE_NAME")) or "Unknown",
        address=clean(attrs.get("LOCATION")),
        city=clean(attrs.get("TOWN")),
        state="MA",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("LATITUDE")),
        lon=parse_float(attrs.get("LONGITUDE")),
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts) if programs_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


def map_pws_source(attrs: dict) -> Facility:
    """Convert a Public Water Supply Source feature to a Facility model.

    Key fields: SOURCE_ID, SITE_NAME, TOWN, PWS_ID, TYPE,
    LATITUDE, LONGITUDE.
    """
    source_id = clean(attrs.get("SOURCE_ID")) or clean(attrs.get("PWS_ID")) or ""

    programs_parts = []
    pws_type = clean(attrs.get("TYPE"))
    if pws_type:
        programs_parts.append(f"Type: {pws_type}")
    pws_id = clean(attrs.get("PWS_ID"))
    if pws_id:
        programs_parts.append(f"PWS: {pws_id}")

    return Facility(
        source=SOURCE,
        source_id=f"pws-{source_id}",
        name=clean(attrs.get("SITE_NAME")) or "Unknown",
        address=None,
        city=clean(attrs.get("TOWN")),
        state="MA",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("LATITUDE")),
        lon=parse_float(attrs.get("LONGITUDE")),
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts) if programs_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
