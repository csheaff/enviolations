"""Map raw Wisconsin DNR ArcGIS feature data to Pydantic models.

WI DNR data comes from ArcGIS at dnrmaps.wi.gov across three services:
  - AM_WARP (Air Management): FACILITY_ID, FACILITY_NAME, LOC_CITY, COUNTY_NAME, NAICS_CODE, SIC_CODE
  - RR_Sites_Map (BRRTS Remediation): ACTIVITY_DETAIL_NO, ACTIVITY_DETAIL_NAME, LOC_ADDR, LOC_CITY
  - WT_SWDV (WPDES Water): FAC_NAME, FAC_SITE_ID, PERMIT_NUMBER, SIC_CODE

Coordinates come from geometry objects (outSR=4326).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords

SOURCE = "wi_dnr"


def map_air_facility(feature: dict) -> Facility:
    """Convert an AM_WARP Air Management feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    facility_id = clean(attrs.get("FACILITY_ID")) or ""
    naics = clean(attrs.get("NAICS_CODE"))
    sic = clean(attrs.get("SIC_CODE"))

    programs = []
    epa_class = clean(attrs.get("EPA_CLASS_CODE"))
    if epa_class:
        programs.append(f"Air-{epa_class}")
    if clean(attrs.get("PART_70_FLAG")) == "Y":
        programs.append("Title V")

    return Facility(
        source=SOURCE,
        source_id=f"air-{facility_id}",
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=None,
        city=clean(attrs.get("LOC_CITY")),
        state="WI",
        zip_code=None,
        county=clean(attrs.get("COUNTY_NAME")),
        lat=lat,
        lon=lon,
        naics_codes=naics,
        sic_codes=sic,
        programs=", ".join(programs) if programs else "Air Management",
        last_updated=datetime.now(timezone.utc),
    )


def map_brrts_site(feature: dict) -> Facility:
    """Convert a BRRTS Remediation site feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    activity_no = clean(attrs.get("ACTIVITY_DETAIL_NO")) or ""
    act_code = clean(attrs.get("ACT_CODE")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"brrts-{activity_no}",
        name=clean(attrs.get("ACTIVITY_DETAIL_NAME")) or "Unknown",
        address=clean(attrs.get("LOC_ADDR")),
        city=clean(attrs.get("LOC_CITY")),
        state="WI",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=f"BRRTS-{act_code}" if act_code else "BRRTS Remediation",
        last_updated=datetime.now(timezone.utc),
    )


def map_wpdes_facility(feature: dict) -> Facility:
    """Convert a WPDES water permit feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    site_id = clean(attrs.get("FAC_SITE_ID")) or clean(attrs.get("FIN")) or ""
    sic = clean(attrs.get("SIC_CODE"))
    permit_no = clean(attrs.get("PERMIT_NUMBER"))

    programs = []
    fac_type = clean(attrs.get("FACILITY_TYPE"))
    if fac_type:
        programs.append(fac_type)
    permit_status = clean(attrs.get("PERMIT_STATUS"))
    if permit_status:
        programs.append(permit_status)

    return Facility(
        source=SOURCE,
        source_id=f"wpdes-{site_id}",
        name=clean(attrs.get("FAC_NAME")) or "Unknown",
        address=None,
        city=None,
        state="WI",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=sic,
        programs=", ".join(programs) if programs else "WPDES",
        last_updated=datetime.now(timezone.utc),
    )
