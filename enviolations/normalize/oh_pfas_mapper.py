"""Map raw Ohio EPA PFAS ArcGIS feature attributes to Pydantic models.

Ohio EPA PFAS data comes from ArcGIS REST services at geo.epa.ohio.gov.
Single layer from DrinkingWater/PFAS_SAMPLING/MapServer/0:
  - Water treatment plants sampled for PFAS compounds
  - ~1,569 records with detection status and phase info
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float, extract_arcgis_coords, extract_zip_from_address

SOURCE = "oh_pfas"


def map_pfas_plant(feature: dict) -> Facility:
    """Convert an OH EPA PFAS sampling record to a Facility.

    Key fields: wtp_name, pwsid, county, sys_type, src_type,
    pop_served, detected, abvactlvl, phase, sys_status.
    Additional fields when available: address, city, zip.
    """
    attrs = feature.get("attributes", feature)

    pwsid = clean(attrs.get("pwsid")) or ""
    name = clean(attrs.get("wtp_name")) or "Unknown"
    lat, lon = extract_arcgis_coords(feature)

    address = (
        clean(attrs.get("address"))
        or clean(attrs.get("ADDRESS"))
        or clean(attrs.get("addr"))
    )
    city = clean(attrs.get("city")) or clean(attrs.get("CITY"))
    zip_code = (
        clean(attrs.get("zip"))
        or clean(attrs.get("ZIP"))
        or clean(attrs.get("zip_code"))
        or clean(attrs.get("ZIP_CODE"))
        or extract_zip_from_address(address)
    )

    programs_parts = ["PFAS Drinking Water Sampling"]
    sys_type = clean(attrs.get("sys_type"))
    if sys_type:
        programs_parts.append(sys_type)
    src_type = clean(attrs.get("src_type"))
    if src_type:
        programs_parts.append(src_type)
    detected = clean(attrs.get("detected"))
    if detected and detected.upper() == "Y":
        programs_parts.append("PFAS Detected")
    abv = clean(attrs.get("abvactlvl"))
    if abv and abv.upper() == "Y":
        programs_parts.append("Above Action Level")

    pop = attrs.get("pop_served")
    pop_str = str(int(pop)) if pop is not None else None

    return Facility(
        source=SOURCE,
        source_id=f"pfas-{pwsid}",
        name=name,
        address=address,
        city=city,
        state="OH",
        zip_code=zip_code,
        county=clean(attrs.get("county")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts),
        last_updated=datetime.now(timezone.utc),
    )
