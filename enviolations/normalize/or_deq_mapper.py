"""Map raw Oregon DEQ ArcGIS feature data to Pydantic models.

OR DEQ data comes from ArcGIS MapServer at arcgis.deq.state.or.us.
All layers in DrinkingWaterProtectionPCS share the same schema:
  DB_ID, Site_ID, Status, COMMON_NM, Address, City, County,
  RET_DATE, Data_Source, PCSCode, PCSType, GWRisk, SWRisk,
  DBShortName, Latitude, Longitude.

Coordinates come from geometry objects (outSR=4326).

Enforcement dataset from deq.state.or.us/programs/enforcement:
  HTML table with 9 columns: Enforcement Number, Program, Region,
  Source Name, Source Location (city), Enforcement Type, Violations,
  Issued date, Penalty Amount.  6,222 records (1998–present).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, extract_arcgis_coords

SOURCE = "or_deq"


def map_facility(feature: dict, layer_label: str = "") -> Facility:
    """Convert a DrinkingWaterProtectionPCS feature to a Facility model.

    All layers share the same field schema. The layer_label is used
    to prefix the source_id and add program context.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    # Fall back to attribute lat/lon if geometry missing
    if lat is None:
        lat = parse_float(attrs.get("Latitude"))
    if lon is None:
        lon = parse_float(attrs.get("Longitude"))

    db_id = clean(attrs.get("DB_ID")) or ""
    site_id = clean(attrs.get("Site_ID")) or db_id
    status = clean(attrs.get("Status"))
    pcs_type = clean(attrs.get("PCSType"))
    db_short = clean(attrs.get("DBShortName")) or ""

    # Build a prefix from DB short name for unique source_id
    prefix = db_short.lower().replace(" ", "-") if db_short else "site"
    source_id = f"{prefix}-{site_id}" if site_id else f"{prefix}-{db_id}"

    programs = []
    if db_short:
        programs.append(db_short)
    if pcs_type:
        programs.append(pcs_type)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("COMMON_NM")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("City")),
        state="OR",
        zip_code=None,
        county=clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else layer_label,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Enforcement HTML table → Facility + Violation
# ---------------------------------------------------------------------------

_REGION_MAP = {
    "ER": "Eastern Region",
    "WR": "Western Region",
    "NW": "Northwest Region",
    "HQ": "Headquarters",
}


def _parse_penalty(raw: str | None) -> float | None:
    """Parse '$1,125' style penalty to float."""
    if not raw:
        return None
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _parse_enf_date(raw: str | None) -> date | None:
    """Parse 'M/D/YYYY' date from enforcement HTML."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _enf_severity(enf_type: str | None, penalty: float | None) -> str:
    """Derive severity from enforcement type and penalty amount."""
    if penalty and penalty >= 10000:
        return "Significant"
    enf_lower = (enf_type or "").lower()
    if "civil penalty" in enf_lower or "penalty" in enf_lower:
        return "High"
    if "warning" in enf_lower or "letter" in enf_lower:
        return "Low"
    if penalty and penalty > 0:
        return "Medium"
    return "Medium"


def _enf_program_area(program: str | None, violations: str | None) -> str:
    """Derive program area from program and violations text."""
    prog = (program or "").strip().upper()
    if prog:
        mapping = {
            "AQ": "Air Quality",
            "WQ": "Water Quality",
            "LQ": "Land Quality",
            "HW": "Hazardous Waste",
            "SW": "Solid Waste",
            "UST": "Underground Storage Tanks",
        }
        for key, val in mapping.items():
            if key in prog:
                return val
    # Try to infer from violations text
    vio = (violations or "").lower()
    if "air" in vio or "emission" in vio:
        return "Air Quality"
    if "water" in vio or "dmr" in vio or "discharge" in vio:
        return "Water Quality"
    if "hazardous" in vio or "rcra" in vio:
        return "Hazardous Waste"
    if "tank" in vio or "ust" in vio:
        return "Underground Storage Tanks"
    return "Environmental Enforcement"


def map_enforcement_facility(row: dict) -> Facility | None:
    """Convert a parsed enforcement HTML row to a Facility.

    Row keys: enf_number, program, region, source_name, location,
              enf_type, violations, issued, penalty
    """
    name = clean(row.get("source_name"))
    if not name:
        return None

    city = clean(row.get("location"))
    # Build a stable source_id from name + city (no unique facility ID)
    name_key = name.lower().replace(" ", "-").replace(",", "")[:60]
    city_key = (city or "unknown").lower().replace(" ", "-")[:20]
    source_id = f"enf-{name_key}-{city_key}"

    program = clean(row.get("program"))
    region = clean(row.get("region"))
    region_name = _REGION_MAP.get(region or "", region or "")

    programs = []
    prog_area = _enf_program_area(program, row.get("violations"))
    programs.append(prog_area)
    if region_name:
        programs.append(region_name)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=name,
        address=None,
        city=city,
        state="OR",
        zip_code=None,
        county=None,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )


def map_enforcement_violation(row: dict) -> Violation | None:
    """Convert a parsed enforcement HTML row to a Violation.

    Row keys: enf_number, program, region, source_name, location,
              enf_type, violations, issued, penalty
    """
    enf_number = clean(row.get("enf_number"))
    if not enf_number:
        return None

    name = clean(row.get("source_name"))
    city = clean(row.get("location"))
    enf_type = clean(row.get("enf_type"))
    violations_text = clean(row.get("violations"))
    penalty = _parse_penalty(row.get("penalty"))
    issued = _parse_enf_date(row.get("issued"))

    # Build facility reference
    if name:
        name_key = name.lower().replace(" ", "-").replace(",", "")[:60]
        city_key = (city or "unknown").lower().replace(" ", "-")[:20]
        fac_source_id = f"enf-{name_key}-{city_key}"
    else:
        fac_source_id = f"enf-orphan-{enf_number}"

    # Description
    desc_parts = []
    if enf_type:
        desc_parts.append(enf_type)
    if violations_text:
        desc_parts.append(violations_text)
    if penalty is not None:
        desc_parts.append(f"Penalty: ${penalty:,.0f}")

    return Violation(
        source=SOURCE,
        source_id=f"enf-{enf_number}",
        facility_source_id=fac_source_id,
        facility_source=SOURCE,
        violation_type=enf_type or "Enforcement Action",
        violation_date=issued,
        statute=None,
        program_area=_enf_program_area(
            row.get("program"), violations_text
        ),
        severity=_enf_severity(enf_type, penalty),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
