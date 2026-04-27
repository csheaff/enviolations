"""Mapper for EPA PFAS Analytic Tools ArcGIS FeatureServer data.

Converts features from 9 priority layers to Facility models. Each layer has
different field names, so each gets its own mapper function.

Source IDs encode the layer type: superfund-{id}, spill-{id}, dod-{id},
industry-{id}, cdr-{id}, tri-offsite-{id}, tri-waste-{id},
emanifest-dest-{id}, emanifest-gen-{id}.

Note: State values in this dataset have a leading space (e.g. " NH").
ZIP codes are sometimes numeric (e.g. 3053 instead of "03053").
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float, extract_arcgis_coords

SOURCE = "epa_pfas"


def _clean_state(val) -> str | None:
    """Clean state value, stripping leading/trailing whitespace."""
    if val is None:
        return None
    v = str(val).strip().upper()
    return v if len(v) == 2 and v.isalpha() else None


def _clean_zip(val) -> str | None:
    """Clean ZIP code, zero-padding numeric values to 5 digits."""
    if val is None:
        return None
    v = str(val).strip()
    if not v:
        return None
    # Numeric ZIPs may lose leading zeros (e.g. 3053 -> "03053")
    if v.isdigit() and len(v) < 5:
        v = v.zfill(5)
    return v


def map_superfund_facility(feature: dict) -> Facility:
    """Layer 2: Superfund PFAS sites."""
    attrs = feature.get("attributes", {})
    site_id = clean(attrs.get("Identifier")) or ""
    if not site_id:
        oid = attrs.get("OBJECTID")
        if oid is not None:
            site_id = str(oid)
        else:
            raise ValueError("Missing Identifier and OBJECTID")

    lat, lon = extract_arcgis_coords(feature)
    if lat is None:
        lat = parse_float(attrs.get("Latitude"))
    if lon is None:
        lon = parse_float(attrs.get("Longitude"))

    npl = clean(attrs.get("NPL_Site"))
    npl_status = clean(attrs.get("NPL_Status"))
    programs = "PFAS Superfund"
    if npl and npl.upper() == "YES":
        programs = "PFAS Superfund, NPL"
        if npl_status and "FINAL" in npl_status.upper():
            programs = "PFAS Superfund, NPL, Federal Superfund"

    return Facility(
        source=SOURCE,
        source_id=f"superfund-{site_id}",
        name=clean(attrs.get("F_Site_Name")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("City")),
        state=_clean_state(attrs.get("State")),
        zip_code=_clean_zip(attrs.get("ZIP_Code")),
        county=clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_spill_facility(feature: dict) -> Facility:
    """Layer 12: Spills/ERNS."""
    attrs = feature.get("attributes", {})
    seq = clean(attrs.get("F_SEQNOS")) or ""
    if not seq:
        oid = attrs.get("OBJECTID")
        if oid is not None:
            seq = str(oid)
        else:
            raise ValueError("Missing F_SEQNOS and OBJECTID")

    lat, lon = extract_arcgis_coords(feature)
    if lat is None:
        lat = parse_float(attrs.get("Latitude"))
    if lon is None:
        lon = parse_float(attrs.get("Longitude"))

    material = clean(attrs.get("Material_Involved"))
    desc = clean(attrs.get("Incident_Description"))
    programs = "PFAS Spill"
    if material:
        programs = f"PFAS Spill ({material})"

    zip_code = _clean_zip(attrs.get("ZIP")) or _clean_zip(attrs.get("Geocoded_ZIP"))

    return Facility(
        source=SOURCE,
        source_id=f"spill-{seq}",
        name=clean(attrs.get("Responsible_Company")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("City")),
        state=_clean_state(attrs.get("State")),
        zip_code=zip_code,
        county=clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_dod_facility(feature: dict) -> Facility:
    """Layer 13: Federal/DoD sites."""
    attrs = feature.get("attributes", {})
    fed_id = clean(attrs.get("Federal_Facility_ID")) or ""
    if not fed_id:
        oid = attrs.get("OBJECTID")
        if oid is not None:
            fed_id = str(oid)
        else:
            raise ValueError("Missing Federal_Facility_ID and OBJECTID")

    lat, lon = extract_arcgis_coords(feature)
    if lat is None:
        lat = parse_float(attrs.get("Latitude"))
    if lon is None:
        lon = parse_float(attrs.get("Longitude"))

    agency = clean(attrs.get("Federal_Agency")) or "Federal"
    presence = clean(attrs.get("PFAS_Presence")) or ""
    programs = f"PFAS Federal Site ({agency})"
    if "known" in presence.lower():
        programs = f"PFAS Federal Site ({agency}), PFAS Detection Confirmed"

    return Facility(
        source=SOURCE,
        source_id=f"dod-{fed_id}",
        name=clean(attrs.get("F_Site_Name")) or "Unknown",
        address=None,
        city=None,
        state=_clean_state(attrs.get("State")),
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_industry_facility(feature: dict) -> Facility:
    """Layer 3: Industry sectors."""
    attrs = feature.get("attributes", {})
    frs_id = clean(attrs.get("F_Identifier")) or ""
    if not frs_id:
        oid = attrs.get("OBJECTID")
        if oid is not None:
            frs_id = str(oid)
        else:
            raise ValueError("Missing F_Identifier and OBJECTID")

    lat, lon = extract_arcgis_coords(feature)
    if lat is None:
        lat = parse_float(attrs.get("Latitude"))
    if lon is None:
        lon = parse_float(attrs.get("Longitude"))

    industry = clean(attrs.get("Industry"))
    epa_programs = clean(attrs.get("EPA_Programs"))
    programs = "PFAS Industry"
    if industry:
        programs = f"PFAS Industry ({industry})"

    # Extract NAICS from various program-specific fields
    naics_parts = []
    for field in ("CAA_NAICS", "CWA_NAICS", "RCRA_NAICS"):
        val = clean(attrs.get(field))
        if val:
            naics_parts.append(val)
    naics = " ".join(naics_parts) if naics_parts else None

    # Extract SIC codes
    sic_parts = []
    for field in ("CAA_SICS", "CWA_SICS"):
        val = clean(attrs.get(field))
        if val:
            sic_parts.append(val)
    sic = " ".join(sic_parts) if sic_parts else None

    return Facility(
        source=SOURCE,
        source_id=f"industry-{frs_id}",
        name=clean(attrs.get("Facility")) or "Unknown",
        address=None,
        city=clean(attrs.get("City")),
        state=_clean_state(attrs.get("State")),
        zip_code=None,
        county=clean(attrs.get("FAC_COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=naics,
        sic_codes=sic,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_cdr_facility(feature: dict) -> Facility:
    """Layer 16: Chemical Data Reporting."""
    attrs = feature.get("attributes", {})
    frs_id = clean(attrs.get("Identifier")) or ""
    if not frs_id:
        oid = attrs.get("OBJECTID")
        if oid is not None:
            frs_id = str(oid)
        else:
            raise ValueError("Missing Identifier and OBJECTID")

    lat, lon = extract_arcgis_coords(feature)
    if lat is None:
        lat = parse_float(attrs.get("Latitude"))
    if lon is None:
        lon = parse_float(attrs.get("Longitude"))

    chemical = clean(attrs.get("Chemical_Name"))
    programs = "PFAS Chemical Data Reporting"
    if chemical:
        programs = f"PFAS CDR ({chemical[:60]})"

    return Facility(
        source=SOURCE,
        source_id=f"cdr-{frs_id}",
        name=clean(attrs.get("F_Facility_Name")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("City")),
        state=_clean_state(attrs.get("State")),
        zip_code=_clean_zip(attrs.get("ZIP_Code")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_tri_offsite_facility(feature: dict) -> Facility:
    """Layer 4: TRI offsite transfers (reporting facility)."""
    attrs = feature.get("attributes", {})
    trifid = clean(attrs.get("TRIFID")) or ""
    if not trifid:
        oid = attrs.get("OBJECTID")
        if oid is not None:
            trifid = str(oid)
        else:
            raise ValueError("Missing TRIFID and OBJECTID")

    lat, lon = extract_arcgis_coords(feature)
    if lat is None:
        lat = parse_float(attrs.get("Latitude"))
    if lon is None:
        lon = parse_float(attrs.get("Longitude"))

    naics_raw = clean(attrs.get("Primary_NAICS_Code"))
    naics = None
    if naics_raw:
        # Format: "325120 Industrial Gas Manufacturing" - extract code
        parts = naics_raw.split()
        if parts and parts[0].isdigit():
            naics = parts[0]

    return Facility(
        source=SOURCE,
        source_id=f"tri-offsite-{trifid}",
        name=clean(attrs.get("Facility_Name")) or "Unknown",
        address=None,
        city=clean(attrs.get("City")),
        state=_clean_state(attrs.get("State")),
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=naics,
        sic_codes=None,
        programs="PFAS TRI Offsite Transfer",
        last_updated=datetime.now(timezone.utc),
    )


def map_tri_waste_facility(feature: dict) -> Facility:
    """Layer 5: TRI waste management."""
    attrs = feature.get("attributes", {})
    frs_id = clean(attrs.get("FRS_ID")) or clean(attrs.get("TRIFID")) or ""
    if not frs_id:
        oid = attrs.get("OBJECTID")
        if oid is not None:
            frs_id = str(oid)
        else:
            raise ValueError("Missing FRS_ID/TRIFID and OBJECTID")

    lat, lon = extract_arcgis_coords(feature)
    if lat is None:
        lat = parse_float(attrs.get("Latitude"))
    if lon is None:
        lon = parse_float(attrs.get("Longitude"))

    naics_raw = clean(attrs.get("Primary_NAICS_Code"))
    naics = None
    if naics_raw:
        parts = naics_raw.split()
        if parts and parts[0].isdigit():
            naics = parts[0]

    return Facility(
        source=SOURCE,
        source_id=f"tri-waste-{frs_id}",
        name=clean(attrs.get("Facility_Name")) or "Unknown",
        address=clean(attrs.get("Street")),
        city=clean(attrs.get("City")),
        state=_clean_state(attrs.get("State")),
        zip_code=_clean_zip(attrs.get("ZIP_Code")),
        county=clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        naics_codes=naics,
        sic_codes=None,
        programs="PFAS TRI Waste Management",
        last_updated=datetime.now(timezone.utc),
    )


def map_emanifest_dest_facility(feature: dict) -> Facility:
    """Layer 9: eManifest destinations."""
    attrs = feature.get("attributes", {})
    des_id = clean(attrs.get("DES_FACILITY_ID")) or ""
    if not des_id:
        oid = attrs.get("OBJECTID")
        if oid is not None:
            des_id = str(oid)
        else:
            raise ValueError("Missing DES_FACILITY_ID and OBJECTID")

    lat = parse_float(attrs.get("DES_LATITUDE"))
    lon = parse_float(attrs.get("DES_LONGITUDE"))

    naics_raw = clean(attrs.get("DES_Primary_NAICS"))
    try:
        naics = str(int(float(naics_raw))) if naics_raw else None
    except (ValueError, TypeError):
        naics = None

    return Facility(
        source=SOURCE,
        source_id=f"emanifest-dest-{des_id}",
        name=clean(attrs.get("DES_FACILITY_NAME")) or "Unknown",
        address=None,
        city=clean(attrs.get("DES_FAC_LOCATION_CITY")),
        state=_clean_state(attrs.get("DES_FAC_LOCATION_STATE")),
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=naics,
        sic_codes=None,
        programs="PFAS eManifest Destination",
        last_updated=datetime.now(timezone.utc),
    )


def map_emanifest_gen_facility(feature: dict) -> Facility:
    """Layer 10: eManifest generators."""
    attrs = feature.get("attributes", {})
    gen_id = clean(attrs.get("GENERATOR_ID")) or ""
    if not gen_id:
        oid = attrs.get("OBJECTID")
        if oid is not None:
            gen_id = str(oid)
        else:
            raise ValueError("Missing GENERATOR_ID and OBJECTID")

    lat = parse_float(attrs.get("GEN_LATITUDE"))
    lon = parse_float(attrs.get("GEN_LONGITUDE"))

    naics_raw = clean(attrs.get("GEN_Primary_NAICS"))
    try:
        naics = str(int(float(naics_raw))) if naics_raw else None
    except (ValueError, TypeError):
        naics = None

    return Facility(
        source=SOURCE,
        source_id=f"emanifest-gen-{gen_id}",
        name=clean(attrs.get("GENERATOR_NAME")) or "Unknown",
        address=None,
        city=clean(attrs.get("GENERATOR_LOCATION_CITY")),
        state=_clean_state(attrs.get("GENERATOR_LOCATION_STATE")),
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=naics,
        sic_codes=None,
        programs="PFAS eManifest Generator",
        last_updated=datetime.now(timezone.utc),
    )
