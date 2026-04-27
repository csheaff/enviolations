"""Map raw Colorado CDPHE ArcGIS feature data to Pydantic models.

Colorado CDPHE data comes from ArcGIS MapServer as JSON features.
Two facility datasets:
  - All Active Air Pollution Emitting Facilities → Facility (has Latitude/Longitude)
  - Wastewater Treatment Plants (SWAP) → Facility (geometry provides lat/lon)

CDPHE does not publish a structured violation/enforcement dataset.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float, extract_arcgis_coords

SOURCE = "co_cdphe"


def map_air_facility(attrs: dict) -> Facility:
    """Convert Air Pollution Emitting Facility attributes to a Facility model.

    Key fields: Airs_ID, Facility, Latitude, Longitude, Date_Data_Published.
    """
    airs_id = clean(attrs.get("Airs_ID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"air-{airs_id}",
        name=clean(attrs.get("Facility")) or "Unknown",
        address=None,
        city=None,
        state="CO",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("Latitude")),
        lon=parse_float(attrs.get("Longitude")),
        naics_codes=None,
        sic_codes=None,
        programs="Air",
        last_updated=datetime.now(timezone.utc),
    )


def map_wastewater_plant(feature: dict) -> Facility:
    """Convert Wastewater Treatment Plant feature to a Facility model.

    Key attribute fields: NPDES_ID, Formal_Nam, Permit_Sta, SIC_Code,
    Facility_N, Major_Mino.
    Geometry provides lat/lon (requested as WGS84).
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    npdes_id = clean(attrs.get("NPDES_ID")) or ""

    sic = attrs.get("SIC_Code")
    sic_str = str(int(sic)) if sic is not None else None

    programs = []
    major_minor = clean(attrs.get("Major_Mino"))
    permit_sta = clean(attrs.get("Permit_Sta"))
    if major_minor:
        programs.append(major_minor)
    if permit_sta:
        programs.append(permit_sta)

    return Facility(
        source=SOURCE,
        source_id=f"ww-{npdes_id}",
        name=clean(attrs.get("Facility_N")) or clean(attrs.get("Formal_Nam")) or "Unknown",
        address=None,
        city=None,
        state="CO",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=sic_str,
        programs=", ".join(programs) if programs else "Wastewater",
        last_updated=datetime.now(timezone.utc),
    )
