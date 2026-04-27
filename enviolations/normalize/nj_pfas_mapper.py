"""Map raw NJ DEP PFAS ArcGIS feature attributes to Pydantic models.

NJ DEP PFAS data comes from two ArcGIS REST services:
  - PFAS Source Survey (MapServer/125): facilities surveyed for PFAS sources
  - PFAS Sampling Composite (FeatureServer/141): sampling results across media

NJ has the strictest PFAS standards in the nation (14 ppt PFOA, 13 ppt PFOS).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float, extract_zip_from_address

SOURCE = "nj_pfas"


def map_source_survey_facility(attrs: dict, geometry: dict | None = None) -> Facility:
    """Convert a PFAS Source Survey record to a Facility.

    Key fields: FACILITYNAME, PROGRAMINTERESTID, NJPDESPERMITNUMBER,
    DISCHARGECATEGORYDESCRIPTION, COUNTY, MUNICIPALITY, NAICSCODE,
    SICCODE, XCOORDINATE, YCOORDINATE.
    """
    pi_id = clean(attrs.get("PROGRAMINTERESTID")) or ""
    name = clean(attrs.get("FACILITYNAME")) or "Unknown"

    programs_parts = ["PFAS Source Survey"]
    discharge = clean(attrs.get("DISCHARGECATEGORYDESCRIPTION"))
    if discharge:
        programs_parts.append(discharge)
    permit = clean(attrs.get("NJPDESPERMITNUMBER"))
    if permit:
        programs_parts.append(f"NJPDES: {permit}")

    lat, lon = None, None
    if geometry:
        lon = parse_float(geometry.get("x"), zero_as_none=True)
        lat = parse_float(geometry.get("y"), zero_as_none=True)

    naics = clean(attrs.get("NAICSCODE"))
    sic = clean(attrs.get("SICCODE"))

    return Facility(
        source=SOURCE,
        source_id=f"survey-{pi_id}",
        name=name,
        address=None,
        city=clean(attrs.get("MUNICIPALITY")),
        state="NJ",
        zip_code=None,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=naics,
        sic_codes=sic,
        programs="; ".join(programs_parts),
        last_updated=datetime.now(timezone.utc),
    )


def map_sampling_site(attrs: dict, geometry: dict | None = None) -> Facility:
    """Convert a PFAS Sampling Composite record to a Facility.

    Key fields: DatasetUniqueID, Dataset, Name, Contaminant, Media,
    SampleValue, Units, ExceedFlag, Municipality, County, Address.
    """
    unique_id = clean(attrs.get("DatasetUniqueID")) or ""
    name = clean(attrs.get("Name")) or unique_id or "Unknown"

    programs_parts = ["PFAS Sampling"]
    dataset = clean(attrs.get("Dataset"))
    if dataset:
        programs_parts.append(dataset)
    media = clean(attrs.get("Media"))
    if media:
        programs_parts.append(media)
    exceed = clean(attrs.get("ExceedFlag"))
    if exceed and "above" in exceed.lower():
        programs_parts.append(f"Exceedance: {exceed}")

    lat, lon = None, None
    if geometry:
        lon = parse_float(geometry.get("x"), zero_as_none=True)
        lat = parse_float(geometry.get("y"), zero_as_none=True)

    address = clean(attrs.get("Address"))
    return Facility(
        source=SOURCE,
        source_id=f"sample-{unique_id}",
        name=name,
        address=address,
        city=clean(attrs.get("Municipality")),
        state="NJ",
        zip_code=extract_zip_from_address(address),
        county=clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts),
        last_updated=datetime.now(timezone.utc),
    )
