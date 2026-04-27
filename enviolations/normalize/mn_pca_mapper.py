"""Map raw Minnesota PCA ArcGIS feature data to Pydantic models.

MN PCA data comes from the WIMN/sites ArcGIS MapServer as JSON features.
Primary dataset:
  - WIMN Sites (Layer 1) → Facility (explicit latitude/longitude fields)

Key attribute fields: SITE_ID, NAME, ADDRESS_STREET, ADDRESS_CITY,
ADDRESS_STATE, ADDRESS_ZIP, LATITUDE, LONGITUDE, COUNTY, ACTIVITY,
PROGRAM_NAME, PROGRAM_NAME_LIST, INDUSTRIAL_CLASSIFICATION.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float

SOURCE = "mn_pca"


def map_wimn_site(attrs: dict) -> Facility:
    """Convert a WIMN Sites feature attributes dict to a Facility model.

    Field names are lowercase in the ArcGIS response: site_id, name,
    address_street, address_city, address_state, address_zip, latitude,
    longitude, county, program_name, program_name_list, activity,
    activity_list, industrial_classification, mpca_id.
    """
    # site_id is a numeric Double in the live API — convert to string
    raw_id = attrs.get("site_id") or attrs.get("SITE_ID")
    if raw_id is not None:
        try:
            site_id = str(int(float(raw_id)))
        except (ValueError, TypeError):
            site_id = str(raw_id).strip()
    else:
        site_id = ""
    # Fallback to mpca_id if site_id is empty
    if not site_id:
        mpca = clean(attrs.get("mpca_id") or attrs.get("MPCA_ID"))
        site_id = mpca or ""

    programs = (clean(attrs.get("program_name_list") or attrs.get("PROGRAM_NAME_LIST"))
                or clean(attrs.get("program_name") or attrs.get("PROGRAM_NAME")))
    activity = (clean(attrs.get("activity_list") or attrs.get("ACTIVITY_LIST"))
                or clean(attrs.get("activity") or attrs.get("ACTIVITY")))

    # Combine programs and activity for the programs field
    program_parts = []
    if programs:
        program_parts.append(programs)
    if activity and activity != programs:
        program_parts.append(activity)

    naics = clean(attrs.get("industrial_classification") or attrs.get("INDUSTRIAL_CLASSIFICATION"))

    return Facility(
        source=SOURCE,
        source_id=site_id,
        name=clean(attrs.get("name") or attrs.get("NAME")) or "Unknown",
        address=clean(attrs.get("address_street") or attrs.get("ADDRESS_STREET")),
        city=clean(attrs.get("address_city") or attrs.get("ADDRESS_CITY")),
        state=clean(attrs.get("address_state") or attrs.get("ADDRESS_STATE")) or "MN",
        zip_code=clean(attrs.get("address_zip") or attrs.get("ADDRESS_ZIP")),
        county=clean(attrs.get("county") or attrs.get("COUNTY")),
        lat=parse_float(attrs.get("latitude") or attrs.get("LATITUDE")),
        lon=parse_float(attrs.get("longitude") or attrs.get("LONGITUDE")),
        naics_codes=naics,
        sic_codes=None,
        programs=", ".join(program_parts) if program_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
