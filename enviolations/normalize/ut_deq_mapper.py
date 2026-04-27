"""Map raw Utah DEQ ArcGIS feature data to Pydantic models.

UT DEQ data comes from ArcGIS Online (AGOL) FeatureServer:
  - FacilityUST: DERRID, LOCNAME, LOCSTR, LOCCITY, LOCCOUNTY, LOCSTATE, LOCZIP,
    FACILITYDE, SITEDESC, RELEASE, OPENRELEASE
  - DAQAirEmissionsInventory: DAQ_ID, COMPANY, ADDRESS1, CITY, COUNTY, YEAR
  - TIER2: DERRID, FAC_NAME, FAC_ADDRES, FAC_CITY, FAC_CNTY, FAC_STATE, FAC_ZIP

Coordinates come from geometry (outSR=4326).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float, extract_arcgis_coords
SOURCE = "ut_deq"

def _clean(val) -> str | None:
    return clean(val, sentinel=True, normalize_ws=True)

def map_ust_facility(feature: dict) -> Facility:
    """Convert a FacilityUST feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    derr_id = _clean(attrs.get("DERRID")) or _clean(attrs.get("OBJECTID")) or ""

    site_desc = _clean(attrs.get("SITEDESC"))
    fac_desc = _clean(attrs.get("FACILITYDE"))
    programs = []
    if site_desc:
        programs.append(site_desc)
    if fac_desc:
        programs.append(fac_desc)

    return Facility(
        source=SOURCE,
        source_id=f"ust-{derr_id}",
        name=_clean(attrs.get("LOCNAME")) or "Unknown",
        address=_clean(attrs.get("LOCSTR")),
        city=_clean(attrs.get("LOCCITY")),
        state="UT",
        zip_code=_clean(attrs.get("LOCZIP")),
        county=_clean(attrs.get("LOCCOUNTY")),
        lat=lat,
        lon=lon,
        programs=", ".join(programs) if programs else "UST",
        last_updated=datetime.now(timezone.utc),
    )

def map_air_facility(feature: dict) -> Facility:
    """Convert a DAQAirEmissionsInventory feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    daq_id = _clean(attrs.get("DAQ_ID")) or _clean(attrs.get("OBJECTID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"air-{daq_id}",
        name=_clean(attrs.get("COMPANY")) or "Unknown",
        address=_clean(attrs.get("ADDRESS1")),
        city=_clean(attrs.get("CITY")),
        state="UT",
        county=_clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        programs="Air Emissions",
        last_updated=datetime.now(timezone.utc),
    )

def map_tier2_facility(feature: dict) -> Facility:
    """Convert a TIER2 feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    derr_id = _clean(attrs.get("DERRID")) or _clean(attrs.get("OBJECTID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"tier2-{derr_id}",
        name=_clean(attrs.get("FAC_NAME")) or "Unknown",
        address=_clean(attrs.get("FAC_ADDRES")),
        city=_clean(attrs.get("FAC_CITY")),
        state="UT",
        zip_code=_clean(attrs.get("FAC_ZIP")),
        county=_clean(attrs.get("FAC_CNTY")),
        lat=lat,
        lon=lon,
        programs="Tier II EPCRA",
        last_updated=datetime.now(timezone.utc),
    )
