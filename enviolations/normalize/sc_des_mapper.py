"""Map raw South Carolina DES ArcGIS feature data to Pydantic models.

In 2024, South Carolina reorganized SCDHEC; environmental programs moved to
SC DES. ArcGIS endpoints moved from gis.dhec.sc.gov to gis.des.sc.gov.

Facility datasets:
  - NPDES Individual Permits (water/Water_Permits/MapServer/0) → Facility
  - Public Water Supply Wells (water/Water_PublicWaterSupply/MapServer/1) → Facility
  - Mines (water/Water_Permits/MapServer/16) → Facility
  - State Regulated Dams (water/Water_Permits/MapServer/18) → Facility

The pre-reorg BEHS_Complaints dataset (used for violations) was retired with
the ePermitting system. SC DES does not currently publish a public GIS feed
of violations. The complaint mappers were removed in this version.

Note: field naming differs across layers (3 different lat/lon conventions).
Coordinates come from geometry objects (outSR=4326) or attribute fields.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords

SOURCE = "sc_des"


def map_npdes(feature: dict) -> Facility:
    """Convert an NPDES Individual Permits feature to a Facility model.

    Key fields: PrmtPrmtNum, SiteName, PrmtAddrStreet, PrmtAddrCity,
    PrmtAddrState, PrmtAddrPostalCode, FeatrCounty, PrmtRefPrmtStatDescr,
    PrmtRefPrmtCatgDescr.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    permit_num = clean(attrs.get("PrmtPrmtNum")) or ""
    status = clean(attrs.get("PrmtRefPrmtStatDescr"))
    category = clean(attrs.get("PrmtRefPrmtCatgDescr"))
    programs = []
    if category:
        programs.append(category)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"npdes-{permit_num}",
        name=clean(attrs.get("SiteName")) or "Unknown",
        address=clean(attrs.get("PrmtAddrStreet")),
        city=clean(attrs.get("PrmtAddrCity")),
        state=clean(attrs.get("PrmtAddrState")) or "SC",
        zip_code=clean(attrs.get("PrmtAddrPostalCode")),
        county=clean(attrs.get("FeatrCounty")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "NPDES",
        last_updated=datetime.now(timezone.utc),
    )


def map_pws_well(feature: dict) -> Facility:
    """Convert a Public Water Supply Wells feature to a Facility model.

    Key fields: PWSNO, PWSNAME, FACILITYNA, COUNTY, PWSSTATUS, PWSTYPE, TYPE.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    pws_no = clean(attrs.get("PWSNO")) or ""
    pws_status = clean(attrs.get("PWSSTATUS"))
    pws_type = clean(attrs.get("PWSTYPE"))
    well_type = clean(attrs.get("TYPE"))
    programs = []
    if pws_type:
        programs.append(pws_type)
    if well_type:
        programs.append(well_type)
    if pws_status:
        programs.append(pws_status)

    # Use PWSNAME as name, fall back to FACILITYNA
    name = clean(attrs.get("PWSNAME")) or clean(attrs.get("FACILITYNA")) or "Unknown"

    return Facility(
        source=SOURCE,
        source_id=f"pws-{pws_no}",
        name=name,
        address=None,
        city=None,
        state="SC",
        zip_code=None,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Public Water Supply",
        last_updated=datetime.now(timezone.utc),
    )


def map_mine(feature: dict) -> Facility:
    """Convert a Mines feature to a Facility model.

    Key fields: PrmtPrmtNum, SiteName, SiteCnty, PrmtRefPrmtStatDescr,
    MinesDtlsMinedMatrl.
    Note: lat/lon attribute fields are SiteLatitude/SiteLongitude (differs from other layers).
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    permit_num = clean(attrs.get("PrmtPrmtNum")) or ""
    status = clean(attrs.get("PrmtRefPrmtStatDescr"))
    material = clean(attrs.get("MinesDtlsMinedMatrl"))
    programs = ["Mine"]
    if material:
        programs.append(material)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"mine-{permit_num}",
        name=clean(attrs.get("SiteName")) or "Unknown",
        address=None,
        city=None,
        state="SC",
        zip_code=None,
        county=clean(attrs.get("SiteCnty")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )


def map_dam(feature: dict) -> Facility:
    """Convert a State Regulated Dams feature to a Facility model.

    Key fields: SiteNum, SiteName, FeatrCounty, SiteSiteTypes, FeatrFeatrType,
    DamnoAltId.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    site_num = clean(attrs.get("SiteNum")) or ""
    site_types = clean(attrs.get("SiteSiteTypes"))
    feat_type = clean(attrs.get("FeatrFeatrType"))
    programs = ["Dam"]
    if site_types:
        programs.append(site_types)
    if feat_type:
        programs.append(feat_type)

    return Facility(
        source=SOURCE,
        source_id=f"dam-{site_num}",
        name=clean(attrs.get("SiteName")) or "Unknown",
        address=None,
        city=None,
        state="SC",
        zip_code=None,
        county=clean(attrs.get("FeatrCounty")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )
