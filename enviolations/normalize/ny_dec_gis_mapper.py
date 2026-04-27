"""Map raw NY DEC GIS ArcGIS features to Pydantic models.

NY DEC GIS data comes from gisservices.dec.ny.gov ArcGIS REST services.
Nine layers from the dil_permits_and_regs MapServer:
  - Layer 21: Petroleum Bulk Storage Facilities (~67K) -> Facility
  - Layer 23: Chemical Bulk Storage Facilities (~3.9K) -> Facility
  - Layer 22: Major Oil Storage Facilities (~161) -> Facility
  - Layer 3: Air Facility Registrations (~7.4K) -> Facility
  - Layer 4: Air Permits Title V (~294) -> Facility
  - Layer 5: Air Permits State (~623) -> Facility
  - Layer 12: Inactive Solid Waste Landfills (~1.9K) -> Facility
  - Layer 6: Hazardous Waste Generators (~202) -> Facility
  - Layer 2: Hazardous Waste TSD Facilities (~26) -> Facility

All layers use ArcGIS point geometry for coordinates (outSR=4326).
No violations available from these layers.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords, parse_float


def _clean_addr(val) -> str | None:
    """Strip pipe-delimited metadata from NY DEC address fields.

    Some NY DEC GIS layers store extra notes after a pipe character in address
    fields (e.g. '38-54 VERNON BLVD|Lot Has Multiple Facilities / Permits On
    It. West Side Of Vernon Blvd.').  Only the portion before the first '|' is
    a usable street address; the rest is a human-readable annotation that
    prevents address-based entity resolution matching with EPA ECHO records.
    """
    if val is None:
        return None
    s = str(val)
    if "|" in s:
        s = s.split("|", 1)[0]
    return clean(s)

SOURCE = "ny_dec_gis"


# ---------------------------------------------------------------------------
# Storage layers (PBS / CBS / MOSF) — similar field names
# ---------------------------------------------------------------------------


def map_pbs_facility(feature: dict) -> Facility:
    """Convert a Petroleum Bulk Storage Facilities feature to a Facility.

    Key fields: PBSNO, SITENAME, SITESTR1, SITECITY, SITEZIP, SITETYPE,
    TOTANKS, TOTALCAPAC, OWNERNAME.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    pbs_no = clean(attrs.get("PBSNO")) or ""

    # Build programs from SITETYPE + tank/capacity info
    parts = []
    site_type = clean(attrs.get("SITETYPE"))
    if site_type:
        parts.append(site_type)
    parts.append("Petroleum Bulk Storage")
    programs = ", ".join(parts)

    return Facility(
        source=SOURCE,
        source_id=f"pbs-{pbs_no}",
        name=clean(attrs.get("SITENAME")) or "Unknown",
        address=_clean_addr(attrs.get("SITESTR1")),
        city=clean(attrs.get("SITECITY")),
        state="NY",
        zip_code=clean(attrs.get("SITEZIP")),
        county=clean(attrs.get("SITECOUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_cbs_facility(feature: dict) -> Facility:
    """Convert a Chemical Bulk Storage Facilities feature to a Facility.

    Key fields: CBSNO, SITENAME, SITESTR1, SITECITY, SITEZIP, SITETYPE,
    TOTANKS, TOTALCAPAC.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    cbs_no = clean(attrs.get("CBSNO")) or ""

    parts = []
    site_type = clean(attrs.get("SITETYPE"))
    if site_type:
        parts.append(site_type)
    parts.append("Chemical Bulk Storage")
    programs = ", ".join(parts)

    return Facility(
        source=SOURCE,
        source_id=f"cbs-{cbs_no}",
        name=clean(attrs.get("SITENAME")) or "Unknown",
        address=_clean_addr(attrs.get("SITESTR1")),
        city=clean(attrs.get("SITECITY")),
        state="NY",
        zip_code=clean(attrs.get("SITEZIP")),
        county=clean(attrs.get("SITECOUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_mosf_facility(feature: dict) -> Facility:
    """Convert a Major Oil Storage Facilities feature to a Facility.

    Key fields: MOSFNO, SITENAME, SITESTR1, SITECITY, SITEZIP, SPDESNO,
    TOTALCAPAC, TOTANKS.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    mosf_no = clean(attrs.get("MOSFNO")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"mosf-{mosf_no}",
        name=clean(attrs.get("SITENAME")) or "Unknown",
        address=_clean_addr(attrs.get("SITESTR1")),
        city=clean(attrs.get("SITECITY")),
        state="NY",
        zip_code=clean(attrs.get("SITEZIP")),
        county=clean(attrs.get("SITECOUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Major Oil Storage",
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Air layers
# ---------------------------------------------------------------------------


def _air_source_id(attrs: dict, prefix: str) -> str:
    """Build a source_id for an air layer, using DEC_ID with fallback."""
    dec_id = clean(attrs.get("DEC_ID"))
    if dec_id:
        return f"{prefix}-{dec_id}"
    appl_id = clean(attrs.get("APPL_ID"))
    if appl_id:
        return f"{prefix}-{appl_id}"
    obj_id = clean(attrs.get("OBJECTID"))
    if obj_id:
        return f"{prefix}-obj{obj_id}"
    return f"{prefix}-"


def map_air_reg_facility(feature: dict) -> Facility:
    """Convert an Air Facility Registrations feature to a Facility.

    Key fields: FACILITY_NAME, DEC_ID, APPL_ID, coordinates.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    return Facility(
        source=SOURCE,
        source_id=_air_source_id(attrs, "air"),
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=_clean_addr(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="NY",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=clean(attrs.get("SIC")),
        programs="Air Facility Registration",
        last_updated=datetime.now(timezone.utc),
    )


def map_air_titlev_facility(feature: dict) -> Facility:
    """Convert an Air Permits Title V feature to a Facility.

    Key fields: FACILITY_NAME, DEC_ID, SIC, county, city,
    emissions (VOC/NOx/CO/CO2/PM/SO2/HAPs in tons).
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    dec_id = clean(attrs.get("DEC_ID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"airv-{dec_id}",
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=_clean_addr(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="NY",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=clean(attrs.get("SIC")),
        programs="Air Title V",
        last_updated=datetime.now(timezone.utc),
    )


def map_air_state_facility(feature: dict) -> Facility:
    """Convert an Air Permits State feature to a Facility.

    Key fields: FACILITY_NAME, DEC_ID, APPL_ID, permit URL, coordinates.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    return Facility(
        source=SOURCE,
        source_id=_air_source_id(attrs, "airs"),
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=_clean_addr(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="NY",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=clean(attrs.get("SIC")),
        programs="Air State Permit",
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Hazardous Waste / Landfill layers
# ---------------------------------------------------------------------------


def map_landfill_facility(feature: dict) -> Facility:
    """Convert an Inactive Solid Waste Landfills feature to a Facility.

    Layer 12 fields vary; common: SITE_NAME or NAME, ADDRESS, CITY, ZIP,
    COUNTY, OBJECTID.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    # Try multiple ID fields — layer 12 may use different keys
    obj_id = clean(attrs.get("OBJECTID")) or ""
    site_no = clean(attrs.get("SITE_NO")) or clean(attrs.get("SITENO")) or ""
    unique_id = site_no or obj_id

    name = (
        clean(attrs.get("SITE_NAME"))
        or clean(attrs.get("NAME"))
        or clean(attrs.get("SITENAME"))
        or "Unknown"
    )

    return Facility(
        source=SOURCE,
        source_id=f"landfill-{unique_id}",
        name=name,
        address=_clean_addr(attrs.get("ADDRESS")) or _clean_addr(attrs.get("SITESTR1")),
        city=clean(attrs.get("CITY")) or clean(attrs.get("SITECITY")),
        state="NY",
        zip_code=clean(attrs.get("ZIP")) or clean(attrs.get("SITEZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Inactive Landfill",
        last_updated=datetime.now(timezone.utc),
    )


def map_hwgen_facility(feature: dict) -> Facility:
    """Convert a Hazardous Waste Generators feature to a Facility.

    Key fields: FACILITY_N, EPA_ID, ADDRESS, CITY, STATE, ZIP, POINT_X/Y.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    epa_id = clean(attrs.get("EPA_ID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"hwgen-{epa_id}",
        name=clean(attrs.get("FACILITY_N")) or "Unknown",
        address=_clean_addr(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="NY",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Hazardous Waste Generator",
        last_updated=datetime.now(timezone.utc),
    )


def map_hwtsd_facility(feature: dict) -> Facility:
    """Convert a Hazardous Waste TSD Facilities feature to a Facility.

    Key fields: NAME, USEPA_ID, STREET, CITY, STATE, ZIP, LAT/LONG,
    PERMIT_EXPIRATION.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    usepa_id = clean(attrs.get("USEPA_ID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"hwtsd-{usepa_id}",
        name=clean(attrs.get("NAME")) or "Unknown",
        address=_clean_addr(attrs.get("STREET")),
        city=clean(attrs.get("CITY")),
        state="NY",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Hazardous Waste TSD",
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# SPDES Wastewater (layer 18)
# ---------------------------------------------------------------------------


def map_spdes_facility(feature: dict) -> Facility:
    """Convert a SPDES Wastewater Facility feature to a Facility.

    Key fields: SPDES_PERMIT_NUM, DEC_ID, DISTRICT_NAME, PERMITEE_NAME,
    CITY, ZIP, RECEIVING_WATER, DESIGN_FLOW, REF_TYPE.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    permit_num = clean(attrs.get("SPDES_PERMIT_NUM")) or ""

    parts = []
    ref_type = clean(attrs.get("REF_TYPE"))
    if ref_type:
        parts.append(ref_type)
    parts.append("SPDES Wastewater")
    programs = ", ".join(parts)

    return Facility(
        source=SOURCE,
        source_id=f"spdes-{permit_num}",
        name=clean(attrs.get("DISTRICT_NAME")) or "Unknown",
        address=_clean_addr(attrs.get("LOCATION_DIRECTIONS")),
        city=clean(attrs.get("CITY")),
        state="NY",
        zip_code=clean(attrs.get("ZIP")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# MSGP Industrial Stormwater (layer 20)
# ---------------------------------------------------------------------------


def map_msgp_facility(feature: dict) -> Facility:
    """Convert an MSGP Industrial Stormwater feature to a Facility.

    Key fields: SPDES_ID, FACILITY, SECTOR, SIC_CODE, SIC_DESC, STATUS.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    spdes_id = clean(attrs.get("SPDES_ID")) or ""

    parts = []
    sector = clean(attrs.get("SECTOR"))
    sic_desc = clean(attrs.get("SIC_DESC"))
    if sector:
        parts.append(f"Sector {sector}")
    if sic_desc:
        parts.append(sic_desc)
    parts.append("Industrial Stormwater (MSGP)")
    programs = ", ".join(parts)

    sic = attrs.get("SIC_CODE")
    sic_str = str(sic) if sic is not None else None

    return Facility(
        source=SOURCE,
        source_id=f"msgp-{spdes_id}",
        name=clean(attrs.get("FACILITY")) or "Unknown",
        address=None,
        city=None,
        state="NY",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=sic_str,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Permitted Mines (layer 24)
# ---------------------------------------------------------------------------


def map_mine_facility(feature: dict) -> Facility:
    """Convert a Permitted Mines feature to a Facility.

    Key fields: MINEID, MINENAME, PERMITTEENAME, LOCATION, COUNTY, TOWN,
    COMMODITY, STATUS, ACRESAFFECTED.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    mine_id = attrs.get("MINEID")
    mine_id_str = str(mine_id) if mine_id is not None else ""

    commodity = clean(attrs.get("COMMODITY"))
    programs = f"Permitted Mine, {commodity}" if commodity else "Permitted Mine"

    return Facility(
        source=SOURCE,
        source_id=f"mine-{mine_id_str}",
        name=clean(attrs.get("MINENAME")) or "Unknown",
        address=_clean_addr(attrs.get("LOCATION")),
        city=clean(attrs.get("TOWN")),
        state="NY",
        zip_code=None,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )
