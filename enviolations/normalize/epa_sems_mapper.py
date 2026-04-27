"""Mapper for EPA SEMS (Superfund Enterprise Management System) ArcGIS data.

Converts features from the FRS_INTERESTS/SEMS MapServer layer to Facility models.

Key fields:
  - REGISTRY_ID: EPA FRS Registry ID (shared with ECHO/RCRA/CAA/SDWA)
  - PRIMARY_NAME: Facility name
  - LOCATION_ADDRESS, CITY_NAME, STATE_CODE, POSTAL_CODE, COUNTY_NAME
  - LATITUDE83, LONGITUDE83: Coordinates
  - PGM_SYS_ID: CERCLIS/SEMS site ID (e.g., "MDN000306636")
  - INTEREST_TYPE: "SUPERFUND NPL" or "SUPERFUND (NON-NPL)"
  - ACTIVE_STATUS: "CURRENTLY ON THE FINAL NPL", "DELETED FROM THE FINAL NPL",
    "NOT ON THE NPL"
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float

SOURCE = "epa_sems"


# EPA administrative placeholders that are not geographic county names.
_NON_COUNTY_VALUES = frozenset({
    "PORTABLE SOURCE",
    "-- NOT DEFINED --",
    "NOT DEFINED",
    "UNKNOWN",
    "N/A",
})


def _clean_county(val) -> str | None:
    """Return None for known non-geographic EPA county placeholders."""
    cleaned = clean(val)
    if cleaned is None:
        return None
    if cleaned.upper() in _NON_COUNTY_VALUES:
        return None
    return cleaned


def _build_programs(interest_type: str | None, active_status: str | None) -> str:
    """Map INTEREST_TYPE and ACTIVE_STATUS to scoring-relevant program names.

    Program names are chosen to trigger the correct scoring deductions:
      - "Federal Superfund" / "NPL" → -30 pts (highest risk)
      - "Superfund" → -25 pts
      - "SEMS" / "CERCLIS" → -20 pts
    """
    programs = ["SEMS"]

    it = (interest_type or "").upper()
    status = (active_status or "").upper()

    if "NON-NPL" in it:
        programs = ["SEMS"]  # Non-NPL: screened but not priority-listed
    elif "NPL" in it:
        if "CURRENTLY ON" in status:
            programs = ["Federal Superfund", "NPL", "SEMS"]
        elif "DELETED" in status:
            programs = ["Superfund", "SEMS"]  # Delisted from NPL = cleanup done
        else:
            programs = ["Superfund", "NPL", "SEMS"]

    return ", ".join(programs)


def map_facility(attrs: dict) -> Facility:
    """Convert an EPA SEMS ArcGIS feature attributes dict to a Facility."""
    registry_id = clean(attrs.get("REGISTRY_ID"))
    if not registry_id:
        raise ValueError("Missing REGISTRY_ID")

    name = clean(attrs.get("PRIMARY_NAME")) or "Unknown"
    state = clean(attrs.get("STATE_CODE"))
    if state:
        state = state.upper()[:2]

    programs = _build_programs(
        attrs.get("INTEREST_TYPE"),
        attrs.get("ACTIVE_STATUS"),
    )

    return Facility(
        source=SOURCE,
        source_id=registry_id,
        name=name,
        address=clean(attrs.get("LOCATION_ADDRESS")),
        city=clean(attrs.get("CITY_NAME")),
        state=state,
        zip_code=clean(attrs.get("POSTAL_CODE")),
        county=_clean_county(attrs.get("COUNTY_NAME")),
        lat=parse_float(attrs.get("LATITUDE83")),
        lon=parse_float(attrs.get("LONGITUDE83")),
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )
