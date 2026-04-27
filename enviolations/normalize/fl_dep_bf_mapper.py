"""Map raw FL DEP Brownfield and PFAS ArcGIS features to Pydantic models.

FL DEP Brownfield Sites (BROWNFIELD_AREAS/MapServer/1) and PFAS Cleanup
Sites (CLEANUP_SP/MapServer/4) from ArcGIS REST API at ca.dep.state.fl.us.

Brownfield sites: BROWNFIELD_AREAS/MapServer/1 returns polygon geometry
(rings), so geometry-based coordinates are unavailable. Coordinates are
extracted from LATITUDE/LONGITUDE attributes (decimal degrees), with a
DMS fallback (LAT_DD/LAT_MM/LAT_SS, LONG_DD/LONG_MM/LONG_SS).

PFAS sites (on the same CLEANUP_SP service as ERIC) may use DMS fields
like the existing ERIC layer; we try ArcGIS geometry first, then DMS.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords, parse_float

# Matches a 5-digit zip (optionally followed by -XXXX) anywhere in a string.
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")

SOURCE = "fl_dep_bf"


def _dms_to_decimal(dd, mm, ss) -> float | None:
    """Convert degrees/minutes/seconds to decimal degrees."""
    d = parse_float(dd)
    m = parse_float(mm)
    s = parse_float(ss)
    if d is None:
        return None
    m = m or 0.0
    s = s or 0.0
    sign = -1 if d < 0 else 1
    return sign * (abs(d) + m / 60.0 + s / 3600.0)


def _extract_zip(zip_val, address: str | None = None) -> str | None:
    """Extract a valid 5-digit zip from an attribute value or address string.

    ArcGIS returns ZIP5 as an integer; 0 means "no data" and is filtered out.
    Falls back to regex extraction from the address string when the attribute
    is absent or zero.
    """
    z = clean(zip_val)
    if z and z != "0":
        return z
    # Fallback: try to pull a zip code from the address string
    if address:
        m = _ZIP_RE.search(address)
        if m:
            return m.group(1)
    return None


def map_brownfield_site(feature: dict) -> Facility:
    """Convert a Brownfield Areas feature to a Facility model.

    Accepts the full ArcGIS feature dict (with geometry) for coordinates.
    Uses whichever unique ID field is available (BF_SITE_ID, SITE_ID, or
    OBJECTID as fallback).

    The BROWNFIELD_AREAS/MapServer/1 layer returns polygon geometry (rings),
    not point geometry, so extract_arcgis_coords will return None/None.
    Fall back to LATITUDE/LONGITUDE attributes, then DMS fields.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    # Polygon layer: geometry x/y are absent — use coordinate attributes.
    # Try decimal-degree LATITUDE/LONGITUDE first, then DMS fallback.
    if lat is None and lon is None:
        lat = parse_float(attrs.get("LATITUDE"), zero_as_none=True)
        lon = parse_float(attrs.get("LONGITUDE"), zero_as_none=True)

    if lat is None and lon is None:
        lat = _dms_to_decimal(
            attrs.get("LAT_DD"), attrs.get("LAT_MM"), attrs.get("LAT_SS")
        )
        lon = _dms_to_decimal(
            attrs.get("LONG_DD"), attrs.get("LONG_MM"), attrs.get("LONG_SS")
        )
        # FL longitudes are west, ensure negative
        if lon is not None and lon > 0:
            lon = -lon

    # Try likely ID fields in priority order
    site_id = (
        clean(attrs.get("BF_SITE_ID"))
        or clean(attrs.get("SITE_ID"))
        or clean(attrs.get("OBJECTID"))
        or ""
    )

    address = (
        clean(attrs.get("SITE_ADDRESS"))
        or clean(attrs.get("ADDRESS"))
        or clean(attrs.get("STREET"))
    )
    return Facility(
        source=SOURCE,
        source_id=f"bf-{site_id}",
        name=clean(attrs.get("SITE_NAME"))
        or clean(attrs.get("BF_SITE_NAME"))
        or clean(attrs.get("NAME"))
        or "Unknown",
        address=address,
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=_extract_zip(attrs.get("ZIP5"), address)
        or _extract_zip(attrs.get("ZIP"), address)
        or _extract_zip(attrs.get("ZIP_CODE"), address),
        county=clean(attrs.get("COUNTY_NAME")) or clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Brownfield",
        last_updated=datetime.now(timezone.utc),
    )


def map_pfas_site(feature: dict) -> Facility:
    """Convert an ERIC PFAS Cleanup site feature to a Facility model.

    Accepts the full ArcGIS feature dict. Tries geometry-based coords first,
    then falls back to DMS fields (LAT_DD/LAT_MM/LAT_SS, LONG_DD/LONG_MM/LONG_SS)
    which the CLEANUP_SP service uses for ERIC sites.
    """
    attrs = feature.get("attributes", {})

    # Try ArcGIS geometry first
    lat, lon = extract_arcgis_coords(feature)

    # Fall back to DMS if geometry didn't provide coords
    if lat is None and lon is None:
        lat = _dms_to_decimal(
            attrs.get("LAT_DD"), attrs.get("LAT_MM"), attrs.get("LAT_SS")
        )
        lon = _dms_to_decimal(
            attrs.get("LONG_DD"), attrs.get("LONG_MM"), attrs.get("LONG_SS")
        )
        # FL longitudes are west, ensure negative
        if lon is not None and lon > 0:
            lon = -lon

    fac_id = (
        clean(attrs.get("SOURCE_FACILITY_ID"))
        or clean(attrs.get("ERIC_ID"))
        or ""
    )

    return Facility(
        source=SOURCE,
        source_id=f"pfas-{fac_id}",
        name=clean(attrs.get("SOURCE_FACILITY_NAME"))
        or clean(attrs.get("SITE_NAME"))
        or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY_NAME")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("PROGRAM")) or "PFAS Cleanup",
        last_updated=datetime.now(timezone.utc),
    )
