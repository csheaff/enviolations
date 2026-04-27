"""Map raw Alaska DEC ArcGIS feature data to Pydantic models.

Alaska DEC data comes from ArcGIS REST services at dec.alaska.gov.
Three facility datasets:
  - Contaminated Sites (SPAR FeatureServer/1): Hazard_ID, Site_Name, Status,
    LUST_Site, Address_1, City, Zip_Code, Borough, Latitude, Longitude
  - Solid Waste Sites (EH FeatureServer/3): siteId, siteName, SiteStatus,
    Classification, city, latitude, longitude
  - Active Regulated UST (SPAR FeatureServer/0): FacilityNbr, FacilityName,
    USTFacilityType, Borough, Latitude, Longitude
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "ak_dec"

def map_contaminated_site(feature: dict) -> Facility:
    """Convert a Contaminated Sites feature to a Facility.

    Key fields: Hazard_ID, Site_Name, File_Number, Status, LUST_Site,
    Address_1, City, Zip_Code, State, Borough, Latitude, Longitude.
    """
    attrs = feature.get("attributes", {})

    hazard_id = attrs.get("Hazard_ID")
    hazard_id_str = str(int(hazard_id)) if hazard_id is not None else ""
    source_id = f"contam-{hazard_id_str}" if hazard_id_str else f"contam-{attrs.get('OBJECTID', '')}"

    lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    lon = parse_float(attrs.get("Longitude"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    programs = []
    status = clean(attrs.get("Status"))
    if status:
        programs.append(status)
    lust = clean(attrs.get("LUST_Site"))
    if lust and lust.lower() == "yes":
        programs.append("LUST")
    if not programs:
        programs.append("Contaminated Site")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("Site_Name")) or "Unknown",
        address=clean(attrs.get("Address_1")),
        city=clean(attrs.get("City")),
        state="AK",
        zip_code=clean(attrs.get("Zip_Code")),
        county=clean(attrs.get("Borough")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_solid_waste(feature: dict) -> Facility:
    """Convert a Solid Waste Sites feature to a Facility.

    Key fields: siteId, siteName, SiteStatus, Classification,
    PermitStatus, city, state, zipCode, latitude, longitude.
    """
    attrs = feature.get("attributes", {})

    site_id = clean(attrs.get("siteId")) or str(attrs.get("OBJECTID", ""))
    source_id = f"sw-{site_id}"

    lat = parse_float(attrs.get("latitude"), zero_as_none=True)
    lon = parse_float(attrs.get("longitude"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    programs = []
    classification = clean(attrs.get("Classification"))
    site_status = clean(attrs.get("SiteStatus"))
    if classification:
        programs.append(classification)
    if site_status:
        programs.append(site_status)
    if not programs:
        programs.append("Solid Waste")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("siteName")) or "Unknown",
        address=None,
        city=clean(attrs.get("city")),
        state="AK",
        zip_code=clean(attrs.get("zipCode")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_ust(feature: dict) -> Facility:
    """Convert an Active Regulated UST Facility feature to a Facility.

    Key fields: FacilityNbr, FacilityName, USTFacilityType,
    Borough, Latitude, Longitude.
    """
    attrs = feature.get("attributes", {})

    fac_nbr = attrs.get("FacilityNbr")
    fac_nbr_str = str(int(fac_nbr)) if fac_nbr is not None else str(attrs.get("OBJECTID", ""))
    source_id = f"ust-{fac_nbr_str}"

    lat = parse_float(attrs.get("Latitude"), zero_as_none=True)
    lon = parse_float(attrs.get("Longitude"), zero_as_none=True)
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    programs = ["UST"]
    fac_type = clean(attrs.get("USTFacilityType"))
    if fac_type:
        programs.append(fac_type)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FacilityName")) or "Unknown",
        address=None,
        city=None,
        state="AK",
        zip_code=None,
        county=clean(attrs.get("Borough")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )
