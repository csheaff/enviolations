"""Map raw Oklahoma DEQ ArcGIS feature data to Pydantic models.

OK DEQ data comes from ArcGIS at gis.deq.ok.gov across three services:
  - AirWeb/MapServer/8 (Point Source Emissions): Company_ID, Facility_ID, Facility,
    SIC, City, County, Latitude, Longitude, Status, Facility_Classification
  - LandWeb/MapServer/9 (Tier II Facilities): Name, AddressStreet, AddressCity,
    AddressZip, County, Latitude, Longitude, ReportYear
  - WaterWeb/MapServer/9 (NPDES Dischargers): FacilityName, NPDESID,
    PermitStatusDesc, CountyName, PermitSICCode, LatitudeinDecimalDegrees,
    LongitudeinDecimalDegrees

Coordinates come from explicit lat/lon fields or geometry (outSR=4326).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "ok_deq"

def _valid_zip(val) -> str | None:
    """Return zip code only if it looks like a real US postal code."""
    z = clean(val)
    if z is None:
        return None
    # Accept 5-digit or ZIP+4 formats only
    import re
    return z if re.match(r"^\d{5}(-\d{4})?$", z) else None

def map_air_facility(feature: dict) -> Facility:
    """Convert an AirWeb Point Source Emissions feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    fac_id = clean(attrs.get("Facility_ID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("Longitude"), zero_as_none=True)

    sic = clean(attrs.get("SIC"))
    # SIC can come as float like 1389.0
    if sic and "." in sic:
        sic = sic.split(".")[0]

    programs = []
    status = clean(attrs.get("Status"))
    if status:
        programs.append(status)
    classification = clean(attrs.get("Facility_Classification"))
    if classification:
        programs.append(classification)

    return Facility(
        source=SOURCE,
        source_id=f"air-{fac_id}",
        name=clean(attrs.get("Facility")) or clean(attrs.get("Company")) or "Unknown",
        address=clean(attrs.get("Facility_Address")),
        city=clean(attrs.get("City")),
        state="OK",
        zip_code=_valid_zip(attrs.get("Zip_Code")),
        county=clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=sic,
        programs=", ".join(programs) if programs else "Air Emissions",
        last_updated=datetime.now(timezone.utc),
    )

def map_tier2_facility(feature: dict) -> Facility:
    """Convert a LandWeb Tier II facility feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    name = clean(attrs.get("Name")) or "Unknown"
    if lat is None:
        lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("Longitude"), zero_as_none=True)

    # Build a source_id from name + lat/lon since no unique ID exists
    obj_id = clean(attrs.get("OBJECTID")) or ""
    source_id = f"tier2-{obj_id}"

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=name,
        address=clean(attrs.get("AddressStreet")),
        city=clean(attrs.get("AddressCity")),
        state="OK",
        zip_code=clean(attrs.get("AddressZip")),
        county=clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Tier II EPCRA",
        last_updated=datetime.now(timezone.utc),
    )

def map_npdes_facility(feature: dict) -> Facility:
    """Convert a WaterWeb NPDES discharger feature to a Facility."""
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    npdes_id = clean(attrs.get("NPDESID")) or ""
    if lat is None:
        lat = parse_float(attrs.get("LatitudeinDecimalDegrees"), zero_as_none=True)
    if lon is None:
        lon = parse_float(attrs.get("LongitudeinDecimalDegrees"), zero_as_none=True)

    sic = clean(attrs.get("PermitSICCode"))

    programs = []
    permit_status = clean(attrs.get("PermitStatusDesc"))
    if permit_status:
        programs.append(permit_status)

    return Facility(
        source=SOURCE,
        source_id=f"npdes-{npdes_id}",
        name=clean(attrs.get("FacilityName")) or "Unknown",
        address=None,
        city=None,
        state="OK",
        zip_code=None,
        county=clean(attrs.get("CountyName")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=sic,
        programs=", ".join(programs) if programs else "NPDES",
        last_updated=datetime.now(timezone.utc),
    )
