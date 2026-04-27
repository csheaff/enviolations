"""Map raw Kansas KDHE ArcGIS feature data to Pydantic models.

KS KDHE data comes from ArcGIS MapServer at maps.kdhe.ks.gov.
Four facility datasets:
  - Wastewater Permit Facilities (DOE/KDHE_general_programs_ex/MapServer/9)
    → Facility (keims_id key)
  - UIC Well Points (DOE/KDHE_general_programs_ex/MapServer/7)
    → Facility (FACILITY_ID key, deduped at facility level)
  - BWM Solid Waste (DOE/KDHE_general_programs_ex/MapServer/13)
    → Facility (SWKEY key)
  - RTK/Tier II (KDEM/BEH_RTK_4_KDEM/MapServer/0)
    → Facility (FAC_PK_INT_CODE key)

Note: Field naming differs significantly across layers.
Coordinates come from geometry objects (outSR=4326).

RTK/Tier II sentinel coordinate: the KDHE ArcGIS layer places records
with no real location data at a placeholder coordinate of (40.0, -94.4).
This point is in Missouri, not Kansas — using it causes the state-mismatch
filter in search_radius() to silently drop all Kansas facilities returned
near that coordinate.  The mapper detects this sentinel and sets lat/lon to
None so those records are treated as ungeocoded.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords

SOURCE = "ks_kdhe"


def _clean(val) -> str | None:
    return clean(val, sentinel=True, normalize_ws=True)


def _double_to_id(val) -> str:
    """Convert a Double field to a clean string ID."""
    if val is None:
        return ""
    try:
        return str(int(val))
    except (ValueError, TypeError):
        return str(val).strip()


def map_wastewater(feature: dict) -> Facility:
    """Convert a Wastewater Permit Facility feature to a Facility model.

    Key fields: keims_id, FAC_NAME, ADDRESS, CITY, COUNTY, LATITUDE, LONGITUDE,
    FAC_TYPE, STATUS, NPDES_NO.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    keims_id = _clean(attrs.get("keims_id")) or ""
    status = _clean(attrs.get("STATUS"))
    fac_type = _clean(attrs.get("FAC_TYPE"))
    programs = []
    if fac_type:
        programs.append(fac_type)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"ww-{keims_id}",
        name=_clean(attrs.get("FAC_NAME")) or "Unknown",
        address=_clean(attrs.get("ADDRESS")),
        city=_clean(attrs.get("CITY")),
        state="KS",
        zip_code=None,
        county=_clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Wastewater",
        last_updated=datetime.now(timezone.utc),
    )


def map_uic_well(feature: dict) -> Facility:
    """Convert a UIC Well Points feature to a Facility model.

    Key fields: FACILITY_ID (Double), FAC_NAME, WELL_COUNTY, LATITUDE, LONGITUDE,
    CLASS, STATUS, WELL_TYPE, FACILITY_TYPE.
    Multiple wells per facility — dedup by FACILITY_ID in connector.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    fac_id = _double_to_id(attrs.get("FACILITY_ID"))
    well_class = _clean(attrs.get("CLASS"))
    status = _clean(attrs.get("STATUS"))
    well_type = _clean(attrs.get("WELL_TYPE"))
    programs = ["UIC"]
    if well_class:
        programs.append(well_class)
    if well_type:
        programs.append(well_type)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"uic-{fac_id}",
        name=_clean(attrs.get("FAC_NAME")) or "Unknown",
        address=None,
        city=None,
        state="KS",
        zip_code=None,
        county=_clean(attrs.get("WELL_COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )


def map_solid_waste(feature: dict) -> Facility:
    """Convert a BWM Solid Waste feature to a Facility model.

    Key fields: SWKEY, FACNAME, LATITUDE, LONGITUDE, PERMITYPE, status.
    Note: FACNAME (no underscore), status (lowercase).
    Very sparse — no address, city, county, or zip.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    swkey = _clean(attrs.get("SWKEY")) or ""
    permit_type = _clean(attrs.get("PERMITYPE"))
    status = _clean(attrs.get("status"))
    programs = []
    if permit_type:
        programs.append(permit_type)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"sw-{swkey}",
        name=_clean(attrs.get("FACNAME")) or "Unknown",
        address=None,
        city=None,
        state="KS",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Solid Waste",
        last_updated=datetime.now(timezone.utc),
    )


def _is_rtk_sentinel(lat: float | None, lon: float | None) -> bool:
    """Return True if (lat, lon) is the KDHE RTK placeholder coordinate.

    KDHE places records with no real location data at exactly (40.0, -94.4),
    a point in Missouri.  We accept a small epsilon (1e-4°, ~10 m) to catch
    the floating-point representation stored by SQLite / ArcGIS.
    """
    if lat is None or lon is None:
        return False
    return abs(lat - 40.0) < 1e-4 and abs(lon - (-94.4)) < 1e-4


def map_rtk(feature: dict) -> Facility:
    """Convert an RTK/Tier II feature to a Facility model.

    Key fields: FAC_PK_INT_CODE, CLEAN_FACILITY_NAME, LOCATION, CITY, COUNTY,
    FACILITYLATITUDE, FACILITYLONGITUDE, ENV_INT_TYPE_DESC, STATUS, NAICS.
    Note: lat/lon fields differ from other layers (FACILITYLATITUDE/FACILITYLONGITUDE).
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    if _is_rtk_sentinel(lat, lon):
        lat, lon = None, None

    fac_pk = _clean(attrs.get("FAC_PK_INT_CODE")) or ""
    env_type = _clean(attrs.get("ENV_INT_TYPE_DESC"))
    status = _clean(attrs.get("STATUS"))
    programs = []
    if env_type:
        programs.append(env_type)
    if status:
        programs.append(status)

    naics = _clean(attrs.get("NAICS"))

    return Facility(
        source=SOURCE,
        source_id=f"rtk-{fac_pk}",
        name=_clean(attrs.get("CLEAN_FACILITY_NAME")) or "Unknown",
        address=_clean(attrs.get("LOCATION")),
        city=_clean(attrs.get("CITY")),
        state="KS",
        zip_code=None,
        county=_clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=naics,
        sic_codes=None,
        programs=", ".join(programs) if programs else "RTK/Tier II",
        last_updated=datetime.now(timezone.utc),
    )
