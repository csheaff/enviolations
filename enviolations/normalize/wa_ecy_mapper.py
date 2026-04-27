"""Map raw WA ECY ArcGIS feature attributes to Pydantic models.

WA ECY data comes from ArcGIS REST FeatureServer. The FacilitySiteInteractions
layer (Layer 1) contains facility-program interactions; we deduplicate by FSID
to produce unique facilities.

The same layer also contains enforcement records (InteractionType='ENFORFNL')
which we map to Violation objects via map_enforcement_interaction().
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float

SOURCE = "wa_ecy"


def map_facility_interaction(attrs: dict, geometry: dict | None = None) -> Facility:
    """Convert a FacilitySiteInteractions feature to a Facility model.

    Key fields: FSID, FacilityCommonName, FacilityStreet1, FacilityStreet2,
    FacilityCity, FacilityState, FacilityZip, EcologyProgram,
    InteractionType, InteractionDescription.

    Geometry is returned in WGS84 (outSR=4326) as x/y → lon/lat.
    """
    fsid = clean(attrs.get("FSID")) or ""

    lat = None
    lon = None
    if geometry:
        lon = parse_float(geometry.get("x"))
        lat = parse_float(geometry.get("y"))

    program = clean(attrs.get("EcologyProgram"))

    return Facility(
        source=SOURCE,
        source_id=f"fs-{fsid}",
        name=clean(attrs.get("FacilityCommonName")) or "Unknown",
        address=clean(attrs.get("FacilityStreet1")),
        city=clean(attrs.get("FacilityCity")),
        state=clean(attrs.get("FacilityState")) or "WA",
        zip_code=clean(attrs.get("FacilityZip")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=program,
        last_updated=datetime.now(timezone.utc),
    )


def map_enforcement_interaction(attrs: dict, geometry: dict | None = None) -> Violation:
    """Convert an ENFORFNL interaction feature to a Violation model.

    Enforcement records share the same ArcGIS layer as facility interactions
    but are filtered by InteractionType='ENFORFNL'.

    Key fields: InteractionID, FSID, InterationStartDate (note: typo in
    upstream field name — "Interation" not "Interaction"), EcologyProgram,
    InteractionDescription.
    """
    interaction_id = clean(attrs.get("InteractionID")) or ""
    fsid = clean(attrs.get("FSID")) or ""

    violation_date = None
    raw_date = attrs.get("InterationStartDate")
    if raw_date is not None:
        try:
            violation_date = datetime.fromtimestamp(raw_date / 1000).date()
        except (ValueError, TypeError, OSError):
            violation_date = None

    return Violation(
        source=SOURCE,
        source_id=f"enf-{interaction_id}",
        facility_source_id=f"fs-{fsid}",
        facility_source=SOURCE,
        violation_type="Enforcement Final",
        violation_date=violation_date,
        statute=None,
        program_area=clean(attrs.get("EcologyProgram")),
        severity="Enforcement Action",
        description=clean(attrs.get("InteractionDescription")),
    )
