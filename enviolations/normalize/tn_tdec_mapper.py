"""Map raw Tennessee TDEC ArcGIS feature data to Pydantic models.

TN TDEC data comes from ArcGIS MapServer at tdeconline.tn.gov.
Facility datasets:
  - Air Pollution Control Permits (APC_Permits/MapServer/0)
    → Facility (SITE_ID key, multiple permits per site)
  - Active UST Facilities (UST_Facilities/MapServer/0)
    → Facility (FACILITY_ID key)
  - Remediation Sites (DOR_Sites/MapServer/0)
    → Facility (FACILITY_ID key)
  - Solid Waste Management Permits (SWM_Permits/MapServer/0)
    → Facility (SITE_ID key, multiple permits per site)
Violation datasets:
  - GWP Complaints (GWP_Complaints/MapServer/0) → Facility + Violation (9.2K)

All coordinates in WGS84 (WKID 4326) natively.
SITE_ID is Double on APC/SWM — cast to int for source_id.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, extract_arcgis_coords, epoch_ms_to_date

SOURCE = "tn_tdec"


def _site_id_str(val) -> str:
    """Convert SITE_ID (Double) to a clean string ID."""
    if val is None:
        return ""
    try:
        return str(int(val))
    except (ValueError, TypeError):
        return str(val).strip()


def map_apc_permit(feature: dict) -> Facility:
    """Convert an APC (Air Pollution Control) Permits feature to a Facility model.

    Key fields: SITE_ID, PERMITTEE_NAME, PERMIT_TYPE, SITE_CITY, SITE_ZIP,
    COUNTY, LATITUDE, LONGITUDE.
    Note: Multiple permits per SITE_ID — dedup in connector.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    site_id = _site_id_str(attrs.get("SITE_ID"))
    permit_type = clean(attrs.get("PERMIT_TYPE"))

    return Facility(
        source=SOURCE,
        source_id=f"apc-{site_id}",
        name=clean(attrs.get("PERMITTEE_NAME")) or "Unknown",
        address=None,
        city=clean(attrs.get("SITE_CITY")),
        state="TN",
        zip_code=clean(attrs.get("SITE_ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=permit_type or "Air Permit",
        last_updated=datetime.now(timezone.utc),
    )


def map_ust_facility(feature: dict) -> Facility:
    """Convert a UST (Underground Storage Tank) Facilities feature to a Facility model.

    Key fields: FACILITY_ID, FACILITY_NAME, FACILITY_STATUS, LATITUDE, LONGITUDE.
    Very lean schema — no city/zip/county/address.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    fac_id = clean(attrs.get("FACILITY_ID")) or ""
    status = clean(attrs.get("FACILITY_STATUS"))

    return Facility(
        source=SOURCE,
        source_id=f"ust-{fac_id}",
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=None,
        city=None,
        state="TN",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=f"UST, {status}" if status else "UST",
        last_updated=datetime.now(timezone.utc),
    )


def map_remediation_site(feature: dict) -> Facility:
    """Convert a DOR (Division of Remediation) Sites feature to a Facility model.

    Key fields: FACILITY_ID, PRIMARY_NAME, STATUS, EFO, LATITUDE, LONGITUDE.
    Very lean schema — no city/zip/county/address.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    fac_id = clean(attrs.get("FACILITY_ID")) or ""
    status = clean(attrs.get("STATUS"))

    return Facility(
        source=SOURCE,
        source_id=f"dor-{fac_id}",
        name=clean(attrs.get("PRIMARY_NAME")) or "Unknown",
        address=None,
        city=None,
        state="TN",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=f"Remediation, {status}" if status else "Remediation",
        last_updated=datetime.now(timezone.utc),
    )


def map_swm_permit(feature: dict) -> Facility:
    """Convert an SWM (Solid Waste Management) Permits feature to a Facility model.

    Key fields: SITE_ID, PERMITTEE_NAME, PERMIT_TYPE, SITE_LOCATION,
    SITE_CITY, SITE_ZIP, COUNTY, LATITUDE, LONGITUDE.
    Richest schema — has address (SITE_LOCATION).
    Note: Multiple permits per SITE_ID — dedup in connector.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    site_id = _site_id_str(attrs.get("SITE_ID"))
    permit_type = clean(attrs.get("PERMIT_TYPE"))

    return Facility(
        source=SOURCE,
        source_id=f"swm-{site_id}",
        name=clean(attrs.get("PERMITTEE_NAME")) or "Unknown",
        address=clean(attrs.get("SITE_LOCATION")),
        city=clean(attrs.get("SITE_CITY")),
        state="TN",
        zip_code=clean(attrs.get("SITE_ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=permit_type or "Solid Waste",
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# GWP Complaints (GWP_Complaints/MapServer/0) → Facility + Violation
# ---------------------------------------------------------------------------

def _gwp_division_program(division: str | None) -> str:
    """Map GWP division code to program area."""
    mapping = {
        "GWP": "Groundwater Protection",
        "UST": "Storage Tanks",
        "DOR": "Remediation",
        "SWM": "Solid Waste",
        "WPC": "Water Pollution Control",
        "APC": "Air Pollution Control",
    }
    return mapping.get(division or "", "Environmental Complaint")


def map_gwp_facility(feature: dict) -> Facility:
    """Convert a GWP Complaint feature to a Facility.

    Key fields: SITE_ID, ROW_ID, SITE_LOCATION, SITE_CITY, SITE_ZIP, LATITUDE, LONGITUDE.

    When SITE_ID is present, the facility is keyed gwp-{site_id} and shared
    across all complaints for that site. When SITE_ID is absent (most complaints),
    a per-complaint stub keyed gwp-orphan-{row_id} is created so that the
    corresponding violation (which uses the same key) can link to it.
    """
    attrs = feature.get("attributes", {})
    site_id = _site_id_str(attrs.get("SITE_ID"))

    if site_id:
        source_id = f"gwp-{site_id}"
        name = clean(attrs.get("SITE_LOCATION")) or f"Site {site_id}"
    else:
        row_id = attrs.get("ROW_ID")
        if row_id is None:
            return None
        row_id_str = str(int(row_id)) if isinstance(row_id, float) else str(row_id)
        source_id = f"gwp-orphan-{row_id_str}"
        name = clean(attrs.get("SITE_LOCATION")) or f"Complaint {row_id_str}"

    lat = parse_float(attrs.get("LATITUDE"))
    lon = parse_float(attrs.get("LONGITUDE"))

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=name,
        address=clean(attrs.get("SITE_LOCATION")),
        city=clean(attrs.get("SITE_CITY")),
        state="TN",
        zip_code=clean(attrs.get("SITE_ZIP")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=_gwp_division_program(clean(attrs.get("DIVISION"))),
        last_updated=datetime.now(timezone.utc),
    )


def map_gwp_violation(feature: dict) -> Violation:
    """Convert a GWP Complaint feature to a Violation.

    Key fields: ROW_ID, SITE_ID, DIVISION, DATE_RECEIVED, HOW_RECEIVED.
    """
    attrs = feature.get("attributes", {})
    row_id = attrs.get("ROW_ID")
    if row_id is None:
        return None
    row_id_str = str(int(row_id)) if isinstance(row_id, float) else str(row_id)
    site_id = _site_id_str(attrs.get("SITE_ID"))

    division = clean(attrs.get("DIVISION"))
    how_received = clean(attrs.get("HOW_RECEIVED"))

    desc_parts = []
    if division:
        desc_parts.append(f"Division: {division}")
    if how_received:
        desc_parts.append(f"Received via: {how_received}")
    location = clean(attrs.get("SITE_LOCATION"))
    if location:
        desc_parts.append(location)

    return Violation(
        source=SOURCE,
        source_id=f"gwp-{row_id_str}",
        facility_source_id=f"gwp-{site_id}" if site_id else f"gwp-orphan-{row_id_str}",
        facility_source=SOURCE,
        violation_type="Environmental Complaint",
        violation_date=epoch_ms_to_date(attrs.get("DATE_RECEIVED")),
        statute=None,
        program_area=_gwp_division_program(division),
        severity="Medium",
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
