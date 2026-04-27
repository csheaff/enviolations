"""Map raw Vermont DEC ArcGIS feature data to Pydantic models.

Vermont DEC data comes from ArcGIS MapServer services at anrmaps.vermont.gov.
Four facility datasets:
  - Hazardous Waste Sites (ENVIRON/163): SiteNumber, SiteName, Address, Town,
    Priority, SiteStatus, Source_Of_Contamination, Contaminants, LatY, LongX
  - Hazardous Waste Generators (ENVIRON/160): EPAID, SiteName, Location_Street1,
    Location_City, Location_State, Location_Zip, Location_County_Name, GenStatus
    (coordinates from geometry)
  - UST (FACILITIES/162): TankID, FacilityID, Name, TankStatus, Address, Town,
    State, Zip, DecLat, DecLong (dedup by FacilityID)
  - Landfills (ENVIRON/164): PSINUM, SWFACID, SITENAME, TOWNNAME, STATUS,
    LATITUDE, LONGITUDE
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean, extract_arcgis_coords

SOURCE = "vt_dec"

def map_haz_waste_site(attrs: dict) -> Facility:
    """Convert a Hazardous Waste Sites record to a Facility.

    Has explicit LatY/LongX coordinate fields.
    """
    site_num = attrs.get("SiteNumber")
    site_num_str = str(int(site_num)) if site_num is not None and site_num == site_num else ""
    source_id = f"haz-{site_num_str}" if site_num_str else f"haz-{attrs.get('OBJECTID', '')}"

    programs = []
    priority = clean(attrs.get("Priority"))
    if priority:
        programs.append(priority)
    status = clean(attrs.get("SiteStatus"))
    if status:
        programs.append(status)
    contam_source = clean(attrs.get("Source_Of_Contamination"))
    if contam_source:
        programs.append(contam_source)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SiteName")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("Town")),
        state="VT",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("LatY"), zero_as_none=True),
        lon=parse_float(attrs.get("LongX"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Hazardous Waste Site",
        last_updated=datetime.now(timezone.utc),
    )

def map_haz_waste_generator(feature: dict) -> Facility:
    """Convert a Hazardous Waste Generators feature to a Facility.

    Coordinates come from geometry (outSR=4326).
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    epa_id = clean(attrs.get("EPAID")) or ""
    source_id = f"gen-{epa_id}" if epa_id else f"gen-{attrs.get('ESRI_OID', '')}"

    programs = ["Hazardous Waste Generator"]
    gen_status = clean(attrs.get("GenStatus"))
    if gen_status:
        programs.append(gen_status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SiteName")) or "Unknown",
        address=clean(attrs.get("Location_Street1")),
        city=clean(attrs.get("Location_City")),
        state=clean(attrs.get("Location_State")) or "VT",
        zip_code=clean(attrs.get("Location_Zip")),
        county=clean(attrs.get("Location_County_Name")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_ust(attrs: dict) -> Facility:
    """Convert a UST record to a Facility.

    Multiple tanks per facility — caller dedup by source_id (uses FacilityID).
    Has explicit DecLat/DecLong coordinate fields.
    """
    fac_id = attrs.get("FacilityID")
    fac_id_str = str(int(fac_id)) if fac_id is not None and fac_id == fac_id else ""
    source_id = f"ust-{fac_id_str}" if fac_id_str else f"ust-{attrs.get('TankID', '')}"

    programs = ["UST"]
    status = clean(attrs.get("TankStatus"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("Name")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("Town")),
        state="VT",
        zip_code=clean(attrs.get("Zip")),
        county=None,
        lat=parse_float(attrs.get("DecLat"), zero_as_none=True),
        lon=parse_float(attrs.get("DecLong"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_landfill(attrs: dict) -> Facility:
    """Convert a Landfills record to a Facility.

    Has explicit LATITUDE/LONGITUDE coordinate fields.
    """
    psi_num = clean(attrs.get("PSINUM")) or ""
    source_id = f"lf-{psi_num}" if psi_num else f"lf-{attrs.get('OBJECTID', '')}"

    programs = []
    status = clean(attrs.get("STATUS"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SITENAME")) or "Unknown",
        address=None,
        city=clean(attrs.get("TOWNNAME")),
        state="VT",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("LATITUDE"), zero_as_none=True),
        lon=parse_float(attrs.get("LONGITUDE"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Landfill",
        last_updated=datetime.now(timezone.utc),
    )
