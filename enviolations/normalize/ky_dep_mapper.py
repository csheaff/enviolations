"""Map raw Kentucky DEP ArcGIS feature data to Pydantic models.

KY DEP data comes from ArcGIS at watermaps.ky.gov across three services:
  - Underground_Storage_Tanks (MapServer/0): AI_NAME, ADDRESS_1,
    MAILING_ADDRESS_MUNICIPALITY, MAILING_ADDRESS_STATE_CODE, MAILING_ADDRESS_ZIP,
    COUNTY, LATITUDE, LONGITUDE, AI_ID, SITE_SEQ_ID, AI_TYPE
  - Superfund_Sites (MapServer/0): AI_NAME, ADDRESS_1, CITY, COUNTY,
    LATITUDE, LONGITUDE, AI_ID, SITE_TYPE
  - Brownfields_Superfunds (MapServer/0): AI_NAME, ADDRESS_1, CITY, COUNTY,
    LATITUDE, LONGITUDE, AI_ID

Coordinates come from explicit LATITUDE/LONGITUDE attribute fields or geometry.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float, extract_arcgis_coords
SOURCE = "ky_dep"

def _clean(val) -> str | None:
    return clean(val, sentinel=True, normalize_ws=True)

def map_ust_facility(feature: dict) -> Facility:
    """Convert an Underground Storage Tank feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    ai_id = _clean(attrs.get("AI_ID")) or _clean(attrs.get("SITE_SEQ_ID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    ai_type = _clean(attrs.get("AI_TYPE"))

    return Facility(
        source=SOURCE,
        source_id=f"ust-{ai_id}",
        name=_clean(attrs.get("AI_NAME")) or "Unknown",
        address=_clean(attrs.get("ADDRESS_1")),
        city=_clean(attrs.get("MAILING_ADDRESS_MUNICIPALITY")),
        state="KY",
        zip_code=_clean(attrs.get("MAILING_ADDRESS_ZIP")),
        county=_clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        programs=f"UST, {ai_type}" if ai_type else "UST",
        last_updated=datetime.now(timezone.utc),
    )

def map_superfund_site(feature: dict) -> Facility:
    """Convert a Superfund Sites feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    ai_id = _clean(attrs.get("AI_ID")) or _clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    site_type = _clean(attrs.get("SITE_TYPE"))

    return Facility(
        source=SOURCE,
        source_id=f"superfund-{ai_id}",
        name=_clean(attrs.get("AI_NAME")) or "Unknown",
        address=_clean(attrs.get("ADDRESS_1")),
        city=_clean(attrs.get("CITY")),
        state="KY",
        county=_clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        programs=f"Superfund, {site_type}" if site_type else "Superfund",
        last_updated=datetime.now(timezone.utc),
    )

def map_brownfield(feature: dict) -> Facility:
    """Convert a Brownfields feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    ai_id = _clean(attrs.get("AI_ID")) or _clean(attrs.get("OBJECTID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    return Facility(
        source=SOURCE,
        source_id=f"brownfield-{ai_id}",
        name=_clean(attrs.get("AI_NAME")) or "Unknown",
        address=_clean(attrs.get("ADDRESS_1")),
        city=_clean(attrs.get("CITY")),
        state="KY",
        county=_clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        programs="Brownfield",
        last_updated=datetime.now(timezone.utc),
    )
