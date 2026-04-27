"""Map raw New Mexico NMED ArcGIS feature data to Pydantic models.

NM NMED data comes from multiple ArcGIS servers (mercator.env.nm.gov, x-23.env.nm.gov).

Three facility datasets:
  - Air Facilities (layers 0-6): AI_NAME, PHYSICAL_ADDRESS_LINE_1, MUNICIPALITY,
    COUNTY, STATE, ZIP, AI_STATUS, AI_TYPE, FACILITY_CLASS, LAT, LON
  - PST Facilities: FACILITY_ID, FACILITY_NAME, ADDRESS1, CITY, COUNTY, STATE, ZIP,
    LAT, LON, TANKS, ACTIVE
  - Brownfields: TBA_SITE_NUM, PROPERTY_NAME, STREET_ADDRESS, CITY, COUNTY, STATE,
    ZIP_CODE, LAT, LON

Two violation datasets:
  - LPST Releases: FACILITY_ID, RELEASE_ID, RELEASE_NAME, CURRENT_STATUS,
    STATUS_DATE, CURRENT_PRIORITY, TOTAL_SCO
  - Delivery Prohibition: FACILITY_ID, EFFECTIVE_DATE, TANK_NUMBER, PRODUCT
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import parse_float, clean

SOURCE = "nm_nmed"

def map_air_facility(feature: dict, layer_name: str = "Air") -> Facility:
    """Convert an air_facilities feature to a Facility."""
    attrs = feature.get("attributes", {})

    obj_id = str(attrs.get("OBJECTID", ""))
    name = clean(attrs.get("AI_NAME")) or "Unknown"
    # Use OBJECTID as unique key since there's no dedicated facility ID field
    source_id = f"air-{obj_id}"

    lat = parse_float(attrs.get("LAT"), zero_as_none=True)
    lon = parse_float(attrs.get("LON"), zero_as_none=True)
    if not lat or not lon:
        geom = feature.get("geometry")
        if geom:
            lon = parse_float(geom.get("x"), zero_as_none=True)
            lat = parse_float(geom.get("y"), zero_as_none=True)

    programs = [f"Air - {layer_name}"]
    fac_class = clean(attrs.get("FACILITY_CLASS"))
    if fac_class:
        programs.append(fac_class)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=name,
        address=clean(attrs.get("PHYSICAL_ADDRESS_LINE_1")),
        city=clean(attrs.get("MUNICIPALITY")),
        state="NM",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_pst_facility(feature: dict) -> Facility:
    """Convert a petroleum storage tank facilities feature to a Facility."""
    attrs = feature.get("attributes", {})

    fac_id = clean(attrs.get("FACILITY_ID"))
    source_id = f"pst-{fac_id}" if fac_id else f"pst-{attrs.get('OBJECTID', '')}"

    lat = parse_float(attrs.get("LAT"), zero_as_none=True)
    lon = parse_float(attrs.get("LON"), zero_as_none=True)

    # Use "UST" (Underground Storage Tank) as the canonical program label so
    # the LUST/UST filter chip matches NM NMED PST facilities. Other state
    # sources (AZ, MO, UT, KY, etc.) all use "UST" for the same program type.
    programs = ["UST"]
    active = attrs.get("ACTIVE")
    if active is not None:
        programs.append("Active" if active else "Inactive")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS1")),
        city=clean(attrs.get("CITY")),
        state="NM",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )

def map_brownfield(feature: dict) -> Facility:
    """Convert a Brownfields feature to a Facility."""
    attrs = feature.get("attributes", {})

    site_num = clean(attrs.get("TBA_SITE_NUM"))
    source_id = f"brownfield-{site_num}" if site_num else f"brownfield-{attrs.get('OBJECTID', '')}"

    lat = parse_float(attrs.get("LAT"), zero_as_none=True)
    lon = parse_float(attrs.get("LON"), zero_as_none=True)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("PROPERTY_NAME")) or "Unknown",
        address=clean(attrs.get("STREET_ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="NM",
        zip_code=clean(attrs.get("ZIP_CODE")),
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Brownfield",
        last_updated=datetime.now(timezone.utc),
    )

def map_lpst_facility(feature: dict) -> Facility:
    """Create a Facility from an LPST release feature.

    When the PST FeatureServer is unavailable, we still need facility records
    for LPST violations to link to. LPST data has FACILITY_ID and RELEASE_NAME
    (often the facility name) but no address or coordinates.
    """
    attrs = feature.get("attributes", {})
    facility_id = clean(attrs.get("FACILITY_ID"))
    if not facility_id:
        raise ValueError("Missing FACILITY_ID")

    # RELEASE_NAME often contains the facility name
    name = clean(attrs.get("RELEASE_NAME")) or "Unknown"

    return Facility(
        source=SOURCE,
        source_id=f"pst-{facility_id}",
        name=name,
        address=None,
        city=None,
        state="NM",
        zip_code=None,
        county=None,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        # "LUST" (Leaking Underground Storage Tank) is the canonical label so
        # the LUST/UST filter chip matches these stub facilities.
        programs="LUST",
        last_updated=datetime.now(timezone.utc),
    )

def map_dp_facility(feature: dict) -> Facility:
    """Create a minimal Facility stub from a delivery prohibition feature.

    When the PST FeatureServer is unavailable, creates pst-{facility_id} stubs
    from delivery prohibition data so violations can link to a facility.
    """
    attrs = feature.get("attributes", {})
    facility_id = clean(attrs.get("FACILITY_ID"))
    if not facility_id:
        raise ValueError("Missing FACILITY_ID")

    return Facility(
        source=SOURCE,
        source_id=f"pst-{facility_id}",
        name="Unknown",
        address=None,
        city=None,
        state="NM",
        zip_code=None,
        county=None,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        # Use "UST" so delivery prohibition facility stubs appear in the
        # LUST/UST filter. They represent active storage tank enforcement.
        programs="UST",
        last_updated=datetime.now(timezone.utc),
    )

def _parse_arcgis_date(val) -> date | None:
    """Parse an ArcGIS date field (epoch ms or date string) to a date."""
    if val is None:
        return None
    # ArcGIS commonly returns dates as epoch milliseconds (large integers)
    if isinstance(val, (int, float)) and val > 1_000_000_000:
        try:
            return datetime.fromtimestamp(val / 1000, tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None
    # Fall back to string parsing
    val_str = str(val).strip()
    if not val_str:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(val_str, fmt).date()
        except ValueError:
            continue
    return None

def map_lpst_release(feature: dict) -> Violation:
    """Convert a leaking PST release feature to a Violation."""
    attrs = feature.get("attributes", {})

    release_id = clean(attrs.get("RELEASE_ID"))
    facility_id = clean(attrs.get("FACILITY_ID"))
    release_name = clean(attrs.get("RELEASE_NAME")) or "Unknown release"
    current_status = clean(attrs.get("CURRENT_STATUS")) or "Unknown"
    priority = clean(attrs.get("CURRENT_PRIORITY"))

    source_id = f"lpst-{release_id}" if release_id else f"lpst-obj-{attrs.get('OBJECTID', '')}"
    facility_source_id = f"pst-{facility_id}" if facility_id else ""

    return Violation(
        source=SOURCE,
        source_id=source_id,
        facility_source_id=facility_source_id,
        facility_source=SOURCE,
        violation_type="Leaking Petroleum Storage Tank Release",
        violation_date=_parse_arcgis_date(attrs.get("STATUS_DATE")),
        statute=None,
        program_area="LUST",
        severity=priority,
        description=f"{release_name} - Status: {current_status}",
    )

def map_delivery_prohibition(feature: dict) -> Violation:
    """Convert a delivery prohibition feature to a Violation."""
    attrs = feature.get("attributes", {})

    facility_id = clean(attrs.get("FACILITY_ID"))
    tank_number = clean(attrs.get("TANK_NUMBER")) or "unknown"
    product = clean(attrs.get("PRODUCT")) or "Unknown product"

    source_id = f"dp-{facility_id}-{tank_number}" if facility_id else f"dp-obj-{attrs.get('OBJECTID', '')}"
    facility_source_id = f"pst-{facility_id}" if facility_id else ""

    return Violation(
        source=SOURCE,
        source_id=source_id,
        facility_source_id=facility_source_id,
        facility_source=SOURCE,
        violation_type="Delivery Prohibition",
        violation_date=_parse_arcgis_date(attrs.get("EFFECTIVE_DATE")),
        statute=None,
        program_area="UST",
        severity="Enforcement Action",
        description=f"Delivery prohibition on {product} tank #{tank_number}",
    )
