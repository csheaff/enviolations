"""Map raw PA DEP GIS ArcGIS features to Pydantic models.

PA DEP GIS data comes from PASDA (mapservices.pasda.psu.edu) ArcGIS REST
services. Seven layers from the DEP MapServer:
  - Layer 27: Storage Tanks Active (~11K) -> Facility
  - Layer 18: Land Recycling Cleanup Locations (~23K) -> Facility
  - Layer 5: Captive Hazardous Waste Operations (~5K) -> Facility
  - Layer 9: Commercial Hazardous Waste Operations (~65) -> Facility
  - Layer 20: Municipal Waste Operations (~3K) -> Facility
  - Layer 26: Residual Waste Operations (~2K) -> Facility
  - Layer 0: AML Inventory Points (~13K) -> Facility

All layers use ArcGIS point geometry for coordinates (outSR=4326).
No violations available from these layers.

PASDA uses shapefile format with 10-character field name truncation.
Mappers defensively try both truncated and full field names.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords, parse_float

SOURCE = "pa_dep_gis"


# ---------------------------------------------------------------------------
# Shared helper for DEP layers with common field pattern
# (CLIENT_NAM, SITE_NAME, PRIMARY_FA, SITE_ID, COMPLIANCE, SUB_FACILI)
# ---------------------------------------------------------------------------


def _get(attrs: dict, *keys: str) -> str | None:
    """Try multiple attribute keys, return first non-empty value."""
    for key in keys:
        val = clean(attrs.get(key))
        if val:
            return val
    return None


def _get_city(attrs: dict) -> str | None:
    """Extract city/municipality from attributes, filtering numeric codes.

    PA DEP GIS MUNICIPALI field sometimes contains 4-digit PA municipality
    FIPS codes (e.g. '1975' = Upper Makefield Township) instead of names.
    These numeric codes are useless for entity resolution (they won't match
    EPA city names) and are filtered out so that Tier 2 geo-proximity matching
    can still link the facility to EPA records. CIV-707.
    """
    city = _get(attrs, "MUNICIPALI", "MUNICIPALITY", "CITY")
    if city and city.strip().isdigit():
        return None
    return city


def _map_dep_facility(
    feature: dict, *, prefix: str, default_program: str
) -> Facility:
    """Shared mapper for PASDA DEP layers with common field structure.

    Most DEP layers share CLIENT_NAM/SITE_NAME/PRIMARY_FA/SITE_ID/COMPLIANCE
    fields (with 10-char shapefile truncation). This helper builds a Facility
    from those common fields.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    site_id = (
        _get(attrs, "SITE_ID")
        or _get(attrs, "OBJECTID")
        or ""
    )

    # Name: prefer SITE_NAME, fall back to CLIENT_NAM / PRIMARY_FA
    name = (
        _get(attrs, "SITE_NAME")
        or _get(attrs, "CLIENT_NAM", "CLIENT_NAME")
        or _get(attrs, "PRIMARY_FA", "PRIMARY_FACILITY")
        or "Unknown"
    )

    # Address: some layers have sub-facility or address fields
    address = _get(attrs, "ADDRESS", "FACILITY_A", "FACILITY_ADDRESS")

    # City: try MUNICIPALITY (truncated as MUNICIPALI).
    # Filter numeric codes (PA FIPS municipality codes like '1975'). CIV-707.
    city = _get_city(attrs)

    county = _get(attrs, "COUNTY")

    # Build programs from compliance status + default program.
    # Filter bare boolean values ("YES"/"NO"/"Y"/"N") — these are raw
    # compliance status flags, not program names (CIV-706).
    _BOOL_COMPLIANCE = {"YES", "NO", "Y", "N"}
    parts = []
    compliance = _get(attrs, "COMPLIANCE")
    if compliance and compliance.upper() not in _BOOL_COMPLIANCE:
        parts.append(compliance)
    parts.append(default_program)
    programs = ", ".join(parts)

    return Facility(
        source=SOURCE,
        source_id=f"{prefix}-{site_id}",
        name=name,
        address=address,
        city=city,
        state="PA",
        # Layers 18/5/9/20/26 do not include any zip/address fields in their
        # ArcGIS attribute tables — only name, ID, and status fields. Zip codes
        # are not available from these layers. CIV-792, CIV-812.
        zip_code=_get(attrs, "ZIP_CODE", "ZIP", "ZIPCODE"),
        county=county,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Storage Tanks Active (Layer 27) — different field names
# ---------------------------------------------------------------------------


def map_tank_facility(feature: dict) -> Facility:
    """Convert a Storage Tanks Active feature to a Facility.

    Layer 27 uses shapefile 10-char field name truncation:
      FACILITY_I  = FACILITY_ID
      FACILITY_N  = FACILITY_NAME
      FACILITY_A  = FACILITY_ADDRESS (line 1)
      FACILITY_1  = FACILITY_ADDRESS2 (line 2, usually empty)
      FACILITY_C  = FACILITY_CITY
      FACILITY_S  = FACILITY_STATE
      FACILITY_Z  = FACILITY_ZIP
      FACILITY_2  = FACILITY_COUNTY (county name)
      FACILITY_M  = FACILITY_MUNICIPALITY
      LATITUDE, LONGITUDE = coordinates (also available from geometry)
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    # Fall back to attribute lat/lon if geometry is missing
    if lat is None or lon is None:
        attr_lat = parse_float(_get(attrs, "LATITUDE"))
        attr_lon = parse_float(_get(attrs, "LONGITUDE"))
        if attr_lat is not None and attr_lon is not None:
            lat, lon = attr_lat, attr_lon

    facility_id = (
        _get(attrs, "FACILITY_I", "FACILITY_ID")
        or _get(attrs, "SITE_ID")
        or ""
    )

    name = (
        _get(attrs, "FACILITY_N", "FACILITY_NAME")
        or "Unknown"
    )

    # FACILITY_C contains the city name directly; fall back to MUNICIPALI
    city = _get(attrs, "FACILITY_C", "FACILITY_CITY") or _get_city(attrs)

    # FACILITY_2 contains the county name (spelled out); fall back to COUNTY
    county = _get(attrs, "FACILITY_2", "FACILITY_COUNTY") or _get(attrs, "COUNTY")

    return Facility(
        source=SOURCE,
        source_id=f"tank-{facility_id}",
        name=name,
        address=_get(attrs, "FACILITY_A", "FACILITY_ADDRESS"),
        city=city,
        state="PA",
        # FACILITY_Z is the zip field for Layer 27 (10-char shapefile truncation
        # of FACILITY_ZIP). Also try ZIP_CODE/ZIP for forward compatibility.
        zip_code=_get(attrs, "FACILITY_Z", "FACILITY_ZIP", "ZIP_CODE", "ZIP"),
        county=county,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Storage Tanks",
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Land Recycling Cleanup Locations (Layer 18)
# ---------------------------------------------------------------------------


def map_cleanup_facility(feature: dict) -> Facility:
    """Convert a Land Recycling Cleanup Locations feature to a Facility.

    Remediation/brownfield/Act 2/HSCA sites. source_id: cleanup-{SITE_ID}.
    """
    return _map_dep_facility(feature, prefix="cleanup", default_program="Land Recycling")


# ---------------------------------------------------------------------------
# Captive Hazardous Waste Operations (Layer 5)
# ---------------------------------------------------------------------------


def map_hwcap_facility(feature: dict) -> Facility:
    """Convert a Captive Hazardous Waste Operations feature to a Facility.

    On-site hazardous waste management. source_id: hwcap-{SITE_ID}.
    """
    return _map_dep_facility(feature, prefix="hwcap", default_program="Captive Hazardous Waste")


# ---------------------------------------------------------------------------
# Commercial Hazardous Waste Operations (Layer 9)
# ---------------------------------------------------------------------------


def map_hwcom_facility(feature: dict) -> Facility:
    """Convert a Commercial Hazardous Waste Operations feature to a Facility.

    Off-site TSDFs. source_id: hwcom-{SITE_ID}.
    """
    return _map_dep_facility(feature, prefix="hwcom", default_program="Commercial Hazardous Waste")


# ---------------------------------------------------------------------------
# Municipal Waste Operations (Layer 20)
# ---------------------------------------------------------------------------


def map_mwaste_facility(feature: dict) -> Facility:
    """Convert a Municipal Waste Operations feature to a Facility.

    Landfills, transfer stations, waste processing. source_id: mwaste-{SITE_ID}.
    """
    return _map_dep_facility(feature, prefix="mwaste", default_program="Municipal Waste")


# ---------------------------------------------------------------------------
# Residual Waste Operations (Layer 26)
# ---------------------------------------------------------------------------


def map_rwaste_facility(feature: dict) -> Facility:
    """Convert a Residual Waste Operations feature to a Facility.

    Non-hazardous industrial waste. source_id: rwaste-{SITE_ID}.
    """
    return _map_dep_facility(feature, prefix="rwaste", default_program="Residual Waste")


# ---------------------------------------------------------------------------
# AML Inventory Points (Layer 0)
# ---------------------------------------------------------------------------


def map_aml_facility(feature: dict) -> Facility:
    """Convert an AML Inventory Points feature to a Facility.

    Abandoned Mine Lands problem areas. source_id: aml-{OBJECTID or PROBLEM_AR}.
    Field names may differ from other DEP layers.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    # AML may use PROBLEM_AR(EA) or OBJECTID as unique identifier
    unique_id = (
        _get(attrs, "PROBLEM_AR", "PROBLEM_AREA", "PROBLEM_AREA_NUMBER")
        or _get(attrs, "SITE_ID")
        or _get(attrs, "OBJECTID")
        or ""
    )

    name = (
        _get(attrs, "SITE_NAME", "PROBLEM_AR", "PROBLEM_AREA")
        or _get(attrs, "DESCRIPTION")
        or "Unknown"
    )

    return Facility(
        source=SOURCE,
        source_id=f"aml-{unique_id}",
        name=name,
        address=None,
        city=_get_city(attrs),
        state="PA",
        zip_code=None,
        county=_get(attrs, "COUNTY"),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Abandoned Mine Land",
        last_updated=datetime.now(timezone.utc),
    )
