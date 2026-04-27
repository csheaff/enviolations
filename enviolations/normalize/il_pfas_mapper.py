"""Map raw IL EPA PFAS ArcGIS feature attributes to Pydantic models.

IL EPA PFAS data comes from ArcGIS REST services at
geoservices.epa.illinois.gov. Single layer from
Water/PfasSamplingResults/MapServer/0:
  - Public water system PFAS sampling results
  - ~1,428 records with per-compound concentration fields
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float, extract_arcgis_coords, extract_zip_from_address

SOURCE = "il_pfas"


def map_pfas_system(feature: dict) -> Facility:
    """Convert an IL EPA PFAS sampling record to a Facility.

    Key fields: PWS_ID, PWS_NAME, ENTRY_PT, TYPE, DETECT,
    PFOA, PFOS, LATITUDE, LONGITUDE.
    Additional fields when available: ADDRESS, CITY_NAME, ZIP_CODE.
    """
    attrs = feature.get("attributes", feature)

    pws_id = clean(attrs.get("PWS_ID")) or ""
    name = clean(attrs.get("PWS_NAME")) or "Unknown"
    lat, lon = extract_arcgis_coords(feature)

    address = (
        clean(attrs.get("ADDRESS"))
        or clean(attrs.get("ADDRESS1"))
        or clean(attrs.get("ADDR"))
    )
    city = clean(attrs.get("CITY_NAME")) or clean(attrs.get("CITY"))
    zip_code = (
        clean(attrs.get("ZIP_CODE"))
        or clean(attrs.get("ZIP"))
        or clean(attrs.get("ZIPCODE"))
        or extract_zip_from_address(address)
    )

    programs_parts = ["PFAS Drinking Water Sampling"]
    src_type = clean(attrs.get("TYPE"))
    if src_type:
        programs_parts.append(src_type)
    detect = clean(attrs.get("DETECT"))
    if detect and "detection" in detect.lower() and "no" not in detect.lower():
        programs_parts.append("PFAS Detected")

    return Facility(
        source=SOURCE,
        source_id=f"pfas-{pws_id}",
        name=name,
        address=address,
        city=city,
        state="IL",
        zip_code=zip_code,
        county=clean(attrs.get("COUNTY_SERVED")) or clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts),
        last_updated=datetime.now(timezone.utc),
    )
