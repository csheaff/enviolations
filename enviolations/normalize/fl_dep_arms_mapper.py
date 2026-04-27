"""Map raw FL DEP ARMS ArcGIS features to Pydantic models.

FL DEP Air Resource Management System (ARMS) data comes from ArcGIS REST API
at ca.dep.state.fl.us. One dataset:
  - ARMS/MapServer/0: Air-permitted facilities → Facility

Unlike other FL DEP datasets, ARMS has real coordinates in ArcGIS geometry
(not DMS fields), so the mapper accepts the full feature dict and uses
extract_arcgis_coords() to pull lat/lon.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, extract_arcgis_coords

SOURCE = "fl_dep_arms"


def map_arms_facility(feature: dict) -> Facility:
    """Convert an ARMS air-permitted facility feature to a Facility model.

    Accepts the full ArcGIS feature dict (with geometry) rather than just
    attributes, because ARMS coordinates come from the geometry object.

    Key fields: AIRS_ID, NAME, OWNER, STREET, CITY, ZIP_5, SIC, STATUS,
    FACILITY_TYPE, plus actual/potential emissions for CO, SO2, NOx, VOC,
    PM10, PM2_5.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    airs_id = clean(attrs.get("AIRS_ID")) or ""

    # Build programs from FACILITY_TYPE and STATUS
    facility_type = clean(attrs.get("FACILITY_TYPE"))
    status = clean(attrs.get("STATUS"))
    if facility_type and status:
        programs = f"{facility_type} ({status})"
    elif facility_type:
        programs = facility_type
    elif status:
        programs = status
    else:
        programs = "Air"

    return Facility(
        source=SOURCE,
        source_id=f"arms-{airs_id}",
        name=clean(attrs.get("NAME")) or "Unknown",
        address=clean(attrs.get("STREET")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=clean(attrs.get("ZIP_5")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=clean(attrs.get("SIC")),
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )
