"""Map raw Wyoming DEQ ArcGIS feature data to Pydantic models.

Wyoming DEQ data comes from ArcGIS services at gis.deq.wyo.gov.
Four facility datasets:
  - WYPDES Outfalls (WYPDES_OUTFALLS/0): WYPermitNu, Permittee, FacilityNa,
    PermitType, PermitStat, CountyName, Latitude_O, Longitude_
    (multiple outfalls per permit — dedup by WYPermitNu)
  - Landfills (WDEQ_DATA/289): SiteID, SiteName, SiteType, Address1, City_1,
    StateCode, ZipCode, County, X, Y
  - STP Sites (WDEQ_DATA/279): AltFacilit, LocName, LocStr, loc_city,
    Latitude, Longitude
  - VRP Sites (VRP_Map/1): VRP_Number, Site_Name, Site_Addre, STATUS,
    TYPE_SITE, COUNTY, CONTAMINAN, POINT_X, POINT_Y
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean

SOURCE = "wy_deq"

def map_wypdes(attrs: dict) -> Facility:
    """Convert a WYPDES Outfall record to a Facility.

    Multiple outfalls per permit — caller dedup by source_id (WYPermitNu).
    """
    permit_num = clean(attrs.get("WYPermitNu")) or ""
    source_id = f"wypdes-{permit_num}" if permit_num else f"wypdes-{attrs.get('OBJECTID', '')}"

    programs = ["WYPDES"]
    permit_type = clean(attrs.get("PermitType"))
    if permit_type:
        programs.append(permit_type)
    permit_stat = clean(attrs.get("PermitStat"))
    if permit_stat:
        programs.append(permit_stat)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FacilityNa")) or clean(attrs.get("Permittee")) or "Unknown",
        address=None,
        city=None,
        state="WY",
        zip_code=None,
        county=clean(attrs.get("CountyName")),
        lat=parse_float(attrs.get("Latitude_O"), zero_as_none=True),
        lon=parse_float(attrs.get("Longitude_"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_landfill(attrs: dict) -> Facility:
    """Convert a Solid Waste Landfill record to a Facility."""
    site_id = clean(attrs.get("SiteID")) or ""
    source_id = f"lf-{site_id}" if site_id else f"lf-{attrs.get('FID', attrs.get('OBJECTID', ''))}"

    programs = ["Solid Waste"]
    site_type = clean(attrs.get("SiteType"))
    if site_type:
        programs.append(site_type)
    fac_class = clean(attrs.get("FacilityCl"))
    if fac_class:
        programs.append(fac_class)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SiteName")) or clean(attrs.get("SiteAltern")) or "Unknown",
        address=clean(attrs.get("Address1")),
        city=clean(attrs.get("City_1")),
        state=clean(attrs.get("StateCode")) or "WY",
        zip_code=clean(attrs.get("ZipCode")),
        county=clean(attrs.get("County")),
        lat=parse_float(attrs.get("Y"), zero_as_none=True),
        lon=parse_float(attrs.get("X"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_stp_site(attrs: dict) -> Facility:
    """Convert a Storage Tank Program cleanup site to a Facility."""
    alt_fac = clean(attrs.get("AltFacilit")) or ""
    source_id = f"stp-{alt_fac}" if alt_fac else f"stp-{attrs.get('FID', attrs.get('OBJECTID', ''))}"

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("LocName")) or "Unknown",
        address=clean(attrs.get("LocStr")),
        city=clean(attrs.get("loc_city")),
        state="WY",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("Latitude"), zero_as_none=True),
        lon=parse_float(attrs.get("Longitude"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs="Storage Tank Cleanup",
        last_updated=datetime.now(timezone.utc),
    )

def map_vrp_site(attrs: dict) -> Facility:
    """Convert a VRP (Voluntary Remediation Program) site to a Facility."""
    vrp_num = clean(attrs.get("VRP_Number")) or ""
    source_id = f"vrp-{vrp_num}" if vrp_num else f"vrp-{attrs.get('FID', attrs.get('OBJECTID', ''))}"

    programs = []
    type_site = clean(attrs.get("TYPE_SITE"))
    if type_site:
        programs.append(type_site)
    status = clean(attrs.get("STATUS"))
    if status:
        programs.append(status)
    contam = clean(attrs.get("CONTAMINAN"))
    if contam:
        programs.append(contam)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("Site_Name")) or "Unknown",
        address=clean(attrs.get("Site_Addre")),
        city=None,
        state="WY",
        zip_code=None,
        county=clean(attrs.get("COUNTY")),
        lat=parse_float(attrs.get("POINT_Y"), zero_as_none=True),
        lon=parse_float(attrs.get("POINT_X"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "VRP",
        last_updated=datetime.now(timezone.utc),
    )
