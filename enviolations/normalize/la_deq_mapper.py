"""Map raw Louisiana DEQ ArcGIS feature data to Pydantic models.

LA DEQ data comes from ArcGIS Online FeatureServer as JSON features.
Three facility datasets:
  - Water_Outfalls (FeatureServer/0) → Facility (7,110 outfall records, deduped by MASTER_AI_ID)
  - LDEQ_Brownfield_Sites (FeatureServer/0) → Facility (107 brownfield sites)
  - LDEQ_Debris_Management_Sites (FeatureServer/0) → Facility (413 debris sites)

Enforcement violation dataset:
  - Monthly Excel files from https://www.deq.louisiana.gov/page/enforcement-actions
    Columns: Parish, Enf Action No, AI ID, AI Name, Action Type, Issued Date, Penalty Amount
    The AI ID links violations back to la_deq facility records.

All datasets share the LA DEQ "Agency Interest" (AI) ID as the universal
facility identifier: MASTER_AI_ID, AI_Number, or AI depending on the layer.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from ..models import Facility, Violation
from ._utils import clean, parse_float, extract_arcgis_coords

SOURCE = "la_deq"

# Map enforcement action type codes to human-readable descriptions
_ACTION_TYPE_LABELS: dict[str, str] = {
    "NOV": "Notice of Violation",
    "CO": "Compliance Order",
    "CONOPP": "Compliance Order with No Opportunity to Cure",
    "PA": "Penalty Assessment",
    "NOPP": "Notice of Proposed Penalty",
    "ACO": "Administrative Compliance Order",
    "ANOPP": "Administrative Notice of Proposed Penalty",
    "NOCV": "Notice of Compliance Verification",
    "XP": "Expedited Penalty",
    "RESC": "Rescission",
}


def map_water_outfall(feature: dict) -> Facility:
    """Convert a Water Outfalls feature to a Facility model.

    Key fields: MASTER_AI_ID, MASTER_AI_NAME, PHYSICAL_ADDRESS_LINE_1,
    PHYSICAL_ADDRESS_MUNICIPALITY, PHYSICAL_ADDRESS_STATE_CODE,
    PHYSICAL_ADDRESS_ZIP, PARISH_OR_COUNTY_DESC, Latitude, Longitude,
    PERSET_PERMIT_NO, PERSET_PERMIT_DESC, SUBJECT_ITEM_DESC.
    """
    attrs = feature.get("attributes", {})

    ai_id = attrs.get("MASTER_AI_ID")
    source_id = str(int(ai_id)) if ai_id is not None else ""

    # Prefer explicit lat/lon from attributes, fall back to geometry
    lat = parse_float(attrs.get("Latitude"))
    lon = parse_float(attrs.get("Longitude"))
    if lat is None or lon is None:
        lat, lon = extract_arcgis_coords(feature)

    permit_no = clean(attrs.get("PERSET_PERMIT_NO"))
    permit_desc = clean(attrs.get("PERSET_PERMIT_DESC"))
    programs = []
    if permit_desc:
        programs.append(permit_desc)
    if permit_no:
        programs.append(permit_no)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("MASTER_AI_NAME")) or "Unknown",
        address=clean(attrs.get("PHYSICAL_ADDRESS_LINE_1")),
        city=clean(attrs.get("PHYSICAL_ADDRESS_MUNICIPALITY")),
        state=clean(attrs.get("PHYSICAL_ADDRESS_STATE_CODE")) or "LA",
        zip_code=clean(attrs.get("PHYSICAL_ADDRESS_ZIP")),
        county=clean(attrs.get("PARISH_OR_COUNTY_DESC")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Water Outfall",
        last_updated=datetime.now(timezone.utc),
    )


def map_brownfield_site(attrs: dict) -> Facility:
    """Convert a Brownfield Sites attributes dict to a Facility model.

    Key fields: AI_Number, Property_Name, Address, City, Parish,
    Latitude, Longitude.
    """
    ai_num = attrs.get("AI_Number")
    source_id = str(int(ai_num)) if ai_num is not None else ""

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("Property_Name")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("City")),
        state="LA",
        zip_code=None,
        county=clean(attrs.get("Parish")),
        lat=parse_float(attrs.get("Latitude")),
        lon=parse_float(attrs.get("Longitude")),
        naics_codes=None,
        sic_codes=None,
        programs="Brownfield",
        last_updated=datetime.now(timezone.utc),
    )


def map_debris_site(attrs: dict) -> Facility:
    """Convert a Debris Management Sites attributes dict to a Facility model.

    Key fields: AI, Site_Name, PHYS_LINE_1, PHYS_CITY, Parish,
    LATITUDE, LONGITUDE, Class, ACTIVITY.
    """
    ai = attrs.get("AI")
    source_id = str(int(ai)) if ai is not None else ""

    site_class = clean(attrs.get("Class"))
    activity = clean(attrs.get("ACTIVITY"))
    programs = []
    if site_class:
        programs.append(site_class)
    if activity and activity != site_class:
        programs.append(activity)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("Site_Name")) or "Unknown",
        address=clean(attrs.get("PHYS_LINE_1")),
        city=clean(attrs.get("PHYS_CITY")),
        state="LA",
        zip_code=None,
        county=clean(attrs.get("Parish")),
        lat=parse_float(attrs.get("LATITUDE")),
        lon=parse_float(attrs.get("LONGITUDE")),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Debris Management",
        last_updated=datetime.now(timezone.utc),
    )


def _parse_enforcement_date(val: Any) -> date | None:
    """Parse enforcement date from Excel row value.

    Excel may return datetime objects or date strings.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.fromisoformat(str(val)).date()
    except (ValueError, TypeError):
        return None


def _normalize_action_type(raw: Any) -> str | None:
    """Return human-readable enforcement action type label."""
    code = clean(raw)
    if code is None:
        return None
    return _ACTION_TYPE_LABELS.get(code.upper(), code)


def map_enforcement_facility(row: dict) -> Facility | None:
    """Create a stub Facility from an LDEQ enforcement Excel row.

    Used for AI IDs that appear in enforcement actions but are not covered
    by the Water Outfalls, Brownfield, or Debris ArcGIS datasets (e.g. air,
    solid waste, underground tanks, hazardous waste, radiation facilities).

    Returns None if the row has no usable AI ID.
    """
    ai_id_raw = row.get("ai_id")
    if ai_id_raw is None:
        return None
    try:
        source_id = str(int(ai_id_raw))
    except (ValueError, TypeError):
        return None
    if not source_id:
        return None

    name = clean(row.get("ai_name")) or clean(row.get("respondent")) or "Unknown"
    parish = clean(row.get("parish"))

    # Infer program area from action number prefix (e.g. AE-CN-... → Air Enforcement)
    action_no = clean(row.get("action_no")) or ""
    prefix = action_no.split("-")[0].upper() if action_no else ""
    _PROGRAM_MAP = {
        "WE": "Water Enforcement",
        "AE": "Air Enforcement",
        "SE": "Solid Waste Enforcement",
        "UE": "Underground Storage Tank Enforcement",
        "MM": "Mining/Materials Enforcement",
        "HE": "Hazardous Waste Enforcement",
        "RE": "Radiation Enforcement",
    }
    programs = _PROGRAM_MAP.get(prefix, "LA DEQ Enforcement")

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=name,
        address=None,
        city=None,
        state="LA",
        zip_code=None,
        county=parish,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_enforcement_violation(row: dict) -> Violation:
    """Convert a row from an LDEQ monthly enforcement Excel file to a Violation.

    The Excel files are published at:
      https://www.deq.louisiana.gov/page/enforcement-actions

    Header variations across years are normalised here. The row dict keys are
    the normalised column names (see _ENFORCEMENT_COL_MAP in la_deq.py).

    Normalised keys used here:
      parish, action_no, ai_id, ai_name, action_type, issued_date, penalty_amount
    """
    action_no = clean(row.get("action_no")) or ""
    ai_id_raw = row.get("ai_id")
    try:
        facility_source_id = str(int(ai_id_raw)) if ai_id_raw is not None else ""
    except (ValueError, TypeError):
        facility_source_id = ""

    action_type_raw = clean(row.get("action_type"))
    violation_type = _normalize_action_type(action_type_raw) or action_type_raw

    penalty_raw = row.get("penalty_amount")
    penalty: float | None = None
    try:
        if penalty_raw is not None:
            penalty = float(penalty_raw)
    except (ValueError, TypeError):
        pass

    desc_parts = []
    if action_type_raw:
        desc_parts.append(f"Action type: {action_type_raw}")
    if penalty is not None and penalty > 0:
        desc_parts.append(f"Penalty: ${penalty:,.2f}")

    severity = None
    if penalty is not None and penalty > 0:
        severity = "Penalty"
    elif action_type_raw in ("NOV", "NOCV"):
        severity = "Notice"

    parish = clean(row.get("parish"))

    return Violation(
        source=SOURCE,
        source_id=action_no,
        facility_source_id=facility_source_id,
        facility_source=SOURCE,
        violation_type=violation_type,
        violation_date=_parse_enforcement_date(row.get("issued_date")),
        statute=None,
        program_area=parish,
        severity=severity,
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
