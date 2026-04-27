"""Map raw WI DNR PFAS ArcGIS feature attributes to Pydantic models.

WI DNR PFAS data comes from ArcGIS REST services at dnrmaps.wi.gov.
Two layers from EM_PFAS/EM_PFAS_MAPLAYERS_PUBLIC_EXT/MapServer:
  - Layer 1: Open PFAS sites (active cleanup/investigation)
  - Layer 2: Closed PFAS sites (cleanup completed)
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords, extract_zip_from_address

SOURCE = "wi_pfas"


def map_pfas_site(feature: dict, status: str = "Open") -> Facility:
    """Convert a WI DNR PFAS site record to a Facility.

    Key fields: SITENAME, SOURCES, MEDIA, DWA, SITE_STATUS, NOTES, BOTW.
    Coordinates come from ArcGIS geometry (outSR=4326).
    Additional fields when available: ADDRESS, CITY, COUNTY, ZIP.
    """
    attrs = feature.get("attributes", feature)

    name = clean(attrs.get("SITENAME")) or "Unknown"
    lat, lon = extract_arcgis_coords(feature)

    address = (
        clean(attrs.get("ADDRESS"))
        or clean(attrs.get("SITE_ADDRESS"))
        or clean(attrs.get("ADDR"))
    )
    city = clean(attrs.get("CITY")) or clean(attrs.get("MUNICIPALITY"))
    zip_code = (
        clean(attrs.get("ZIP"))
        or clean(attrs.get("ZIP_CODE"))
        or clean(attrs.get("ZIPCODE"))
        or extract_zip_from_address(address)
    )

    programs_parts = [f"PFAS {status}"]
    sources = clean(attrs.get("SOURCES"))
    if sources:
        programs_parts.append(f"Source: {sources}")
    media = clean(attrs.get("MEDIA"))
    if media:
        programs_parts.append(media)
    dwa = clean(attrs.get("DWA"))
    if dwa and dwa.upper() == "YES":
        programs_parts.append("Drinking Water Advisory")

    object_id = attrs.get("OBJECTID", "")

    return Facility(
        source=SOURCE,
        source_id=f"pfas-{object_id}",
        name=name,
        address=address,
        city=city,
        state="WI",
        zip_code=zip_code,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts),
        last_updated=datetime.now(timezone.utc),
    )
