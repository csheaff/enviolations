"""Map raw MI EGLE PFAS ArcGIS feature attributes to Pydantic models.

MI EGLE PFAS data comes from ArcGIS REST services at gisagoegle.state.mi.us.
Three layers from EGLE/PfasOpenData/MapServer:
  - Layer 0: Surface Water PFAS sampling → Facility (site-level aggregation)
  - Layer 1: Fish Tissue PFAS sampling → Facility (station-level aggregation)
  - Layer 3: Compliance Monitoring (water systems) → Facility (system-level)

All layers are aggregated to facility/site level — individual samples are not
stored. Key PFAS compound concentrations (PFOA, PFOS) are captured in the
facility programs field when detected.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float, epoch_ms_to_date, extract_zip_from_address

SOURCE = "mi_pfas"


def map_surface_water_site(attrs: dict) -> Facility:
    """Convert a surface water PFAS sampling record to a Facility.

    Key fields: SiteCode, Waterbody, Watershed, Latitude, Longitude,
    Project, Description, CAS335671_PFOA, CAS1763231_PFOS.
    """
    site_code = clean(attrs.get("SiteCode")) or ""
    waterbody = clean(attrs.get("Waterbody")) or ""
    watershed = clean(attrs.get("Watershed")) or ""

    name = waterbody or site_code or "Unknown"

    programs_parts = ["PFAS Surface Water Monitoring"]
    project = clean(attrs.get("Project"))
    if project:
        programs_parts.append(project)

    return Facility(
        source=SOURCE,
        source_id=f"sw-{site_code}",
        name=name,
        address=None,
        city=None,
        state="MI",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("Latitude")),
        lon=parse_float(attrs.get("Longitude")),
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts),
        last_updated=datetime.now(timezone.utc),
    )


def map_fish_tissue_station(attrs: dict) -> Facility:
    """Convert a fish tissue PFAS sampling record to a Facility.

    Key fields: StationID, WaterBody, CountyName, SamplingLocation,
    Lat, Long, PFOSppb.
    """
    station_id = attrs.get("StationID")
    station_str = str(int(station_id)) if station_id is not None else ""

    waterbody = clean(attrs.get("WaterBody")) or ""
    location = clean(attrs.get("SamplingLocation")) or ""
    name = location or waterbody or (f"Station {station_str}" if station_str else "Unknown")

    return Facility(
        source=SOURCE,
        source_id=f"ft-{station_str}",
        name=name,
        address=None,
        city=None,
        state="MI",
        zip_code=None,
        county=clean(attrs.get("CountyName")),
        lat=parse_float(attrs.get("Lat")),
        lon=parse_float(attrs.get("Long")),
        naics_codes=None,
        sic_codes=None,
        programs="PFAS Fish Tissue Monitoring",
        last_updated=datetime.now(timezone.utc),
    )


def map_compliance_system(attrs: dict) -> Facility:
    """Convert a compliance monitoring PFAS record to a Facility.

    Key fields: SystemName, WSSN, LocName, SampleDate,
    PFOA, PFOS, PFHxS, PFNA, PFHxA.
    Additional fields when available: Address, City, Zip, County.
    """
    wssn = attrs.get("WSSN")
    wssn_str = str(int(wssn)) if wssn is not None else ""
    system_name = clean(attrs.get("SystemName")) or "Unknown"

    address = (
        clean(attrs.get("Address"))
        or clean(attrs.get("ADDRESS"))
        or clean(attrs.get("LocAddress"))
    )
    city = (
        clean(attrs.get("City"))
        or clean(attrs.get("CITY"))
        or clean(attrs.get("LocName"))
    )
    zip_code = (
        clean(attrs.get("Zip"))
        or clean(attrs.get("ZIP"))
        or clean(attrs.get("ZipCode"))
        or clean(attrs.get("ZIP_CODE"))
        or extract_zip_from_address(address)
    )

    programs_parts = ["PFAS Compliance Monitoring"]
    pfoa = clean(attrs.get("PFOA"))
    pfos = clean(attrs.get("PFOS"))
    if pfoa and pfoa != "ND":
        programs_parts.append(f"PFOA: {pfoa}")
    if pfos and pfos != "ND":
        programs_parts.append(f"PFOS: {pfos}")

    return Facility(
        source=SOURCE,
        source_id=f"cm-{wssn_str}",
        name=system_name,
        address=address,
        city=city,
        state="MI",
        zip_code=zip_code,
        county=clean(attrs.get("County")) or clean(attrs.get("COUNTY")),
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts),
        last_updated=datetime.now(timezone.utc),
    )
