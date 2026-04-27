"""Map raw CA State Water Board data to Pydantic models.

Data from CKAN API at data.ca.gov:
  - CIWQS wastewater violations (322K) → Violation + Facility stub
  - CIWQS wastewater enforcement (52K) → Facility + Violation
  - SMARTS stormwater violations (84K) → Facility + Violation

CIWQS = California Integrated Water Quality System (NPDES wastewater permits)
SMARTS = Stormwater Multiple Application & Report Tracking System
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import parse_date, clean, parse_float

SOURCE = "ca_waterboard"


def _clean(val) -> str | None:
    return clean(val, sentinel=True, normalize_ws=True)


def _normalize_zip(zip_val: str | None) -> str | None:
    """Strip zero-only ZIP+4 suffix (e.g. '92145-0000' → '92145').

    CA State Water Board data frequently contains ZIP+4 codes where the
    extension is '0000', which is a placeholder rather than a real routing
    code. Strip it so all CA zip codes are in standard 5-digit format.
    Non-zero extensions (e.g. '94103-1234') are left unchanged.
    """
    if zip_val is None:
        return None
    if zip_val.endswith("-0000"):
        return zip_val[:-5]
    return zip_val


def _fix_ca_lon(lon: float | None) -> float | None:
    """Negate positive longitude values for California facilities.

    Some CIWQS records have the wrong sign for longitude (e.g., 122.485 instead
    of -122.485). California longitudes must be in the range -114 to -124.5, so
    any positive value in the California range (100-130) is clearly wrong-sign.
    """
    if lon is None:
        return None
    if 100.0 <= lon <= 130.0:
        return -lon
    return lon


def _parse_date_mdy(val: str | None) -> date | None:
    return parse_date(val, formats=("%m/%d/%Y",))


def _parse_date_iso(val: str | None) -> date | None:
    return parse_date(val)


def _ciwqs_severity(rec: dict) -> str | None:
    """Derive severity from CIWQS violation/enforcement fields."""
    status = (_clean(rec.get("STATUS")) or "").lower()
    subtype = (_clean(rec.get("VIOLATION SUBTYPE")) or "").upper()
    # CAT1 = Category 1 (serious), OEV = Other Effluent Violation
    if subtype == "CAT1":
        return "Significant"
    if status == "dismissed":
        return None
    vtype = (_clean(rec.get("VIOLATION TYPE")) or "").lower()
    if "effluent" in vtype:
        return "Medium"
    return "Low"


def _smarts_severity(rec: dict) -> str | None:
    """Derive severity from SMARTS violation fields."""
    serious = (_clean(rec.get("SERIOUS_VIOLATION")) or "").upper()
    if serious == "Y":
        return "Significant"
    priority = (_clean(rec.get("VIOLATION_PRIORITY")) or "").upper()
    if priority == "Y":
        return "Medium"
    return "Low"


def _enforcement_severity(rec: dict) -> str | None:
    """Derive severity from CIWQS enforcement fields."""
    amount = parse_float(rec.get("TOTAL ASSESSMENT AMOUNT"))
    if amount and amount > 100000:
        return "Significant"
    if amount and amount > 10000:
        return "High"
    if amount and amount > 0:
        return "Medium"
    action_type = (_clean(rec.get("ENFORCEMENT ACTION TYPE")) or "").lower()
    if "cease" in action_type or "cleanup" in action_type:
        return "High"
    if "acl" in action_type or "penalty" in action_type:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# CIWQS Wastewater Violations (322K records)
# ---------------------------------------------------------------------------

def has_ciwqs_violation(rec: dict) -> bool:
    """Filter out dismissed or non-violation records."""
    status = (_clean(rec.get("STATUS")) or "").lower()
    if status == "dismissed":
        return False
    vid = _clean(rec.get("VIOLATION ID (VID)"))
    return bool(vid)


def map_ciwqs_violation_facility(rec: dict) -> Facility | None:
    """Create a stub Facility from a CIWQS violation record.

    CIWQS violation records contain FACILITY_ID and FACILITY NAME but no
    address or coordinate data. This creates a minimal facility record so that
    violations for facilities not present in the enforcement dataset are not
    orphaned. The stub will be geo-resolved against EPA ECHO or other sources
    during entity resolution.

    Returns None if no FACILITY_ID is present (WDID-only violations link to
    SMARTS facilities instead).
    """
    fac_id = _clean(rec.get("FACILITY_ID"))
    if not fac_id:
        return None

    name = _clean(rec.get("FACILITY NAME")) or "Unknown"
    program = _clean(rec.get("PROGRAM")) or _clean(rec.get("PROGRAM CATEGORY"))

    return Facility(
        source=SOURCE,
        source_id=f"ciwqs-{fac_id}",
        name=name,
        state="CA",
        programs=program,
        last_updated=datetime.now(timezone.utc),
    )


def map_ciwqs_violation(rec: dict) -> Violation:
    """Convert a CIWQS violation record to a Violation.

    Key fields: VIOLATION ID (VID), FACILITY_ID, FACILITY NAME,
    VIOLATION TYPE, VIOLATION SUBTYPE, VIOLATION DESCRIPTION,
    STATUS, OCCURRED ON, PROGRAM.
    """
    vid = _clean(rec.get("VIOLATION ID (VID)")) or ""
    fac_id = _clean(rec.get("FACILITY_ID"))
    wdid = (_clean(rec.get("WDID")) or "").replace(" ", "") or None

    # Link to facility by FACILITY_ID or WDID.
    # WDID-only violations point to smarts-{wdid} because SMARTS facilities
    # are stored under that prefix — using a different prefix would create
    # orphan violations when SMARTS facilities exist for the same WDID.
    if fac_id:
        facility_source_id = f"ciwqs-{fac_id}"
    elif wdid:
        facility_source_id = f"smarts-{wdid}"
    else:
        facility_source_id = f"ciwqs-orphan-{vid}"

    desc_parts = []
    vtype = _clean(rec.get("VIOLATION TYPE"))
    vsubtype = _clean(rec.get("VIOLATION SUBTYPE"))
    if vtype:
        desc_parts.append(vtype)
    if vsubtype:
        desc_parts.append(f"({vsubtype})")
    vdesc = _clean(rec.get("VIOLATION DESCRIPTION"))
    if vdesc:
        desc_parts.append(vdesc)
    param = _clean(rec.get("Parameter"))
    if param:
        limit_val = _clean(rec.get("Limit"))
        result_val = _clean(rec.get("Result"))
        units = _clean(rec.get("Units"))
        if limit_val and result_val:
            desc_parts.append(f"{param}: {result_val} vs limit {limit_val} {units or ''}")

    program = _clean(rec.get("PROGRAM")) or _clean(rec.get("PROGRAM CATEGORY"))

    return Violation(
        source=SOURCE,
        source_id=f"ciwqs-v-{vid}",
        facility_source_id=facility_source_id,
        facility_source=SOURCE,
        violation_type=vtype or "Wastewater Violation",
        violation_date=_parse_date_mdy(rec.get("OCCURRED ON")),
        statute=_clean(rec.get("REG MEAS TYPE")),
        program_area=program or "Water Quality",
        severity=_ciwqs_severity(rec),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# CIWQS Wastewater Enforcement (52K records) → Facility + Violation
# ---------------------------------------------------------------------------

def map_ciwqs_enforcement_facility(rec: dict) -> Facility | None:
    """Convert a CIWQS enforcement record to a Facility.

    Key fields: FACILITY ID, FACILITY NAME, PLACE ADDRESS, PLACE CITY,
    PLACE ZIP, PLACE COUNTY, PLACE LATITUDE, PLACE LONGITUDE.
    """
    fac_id = _clean(rec.get("FACILITY ID"))
    wdid = (_clean(rec.get("WDID")) or "").replace(" ", "") or None
    if not fac_id and not wdid:
        return None

    source_id = f"ciwqs-{fac_id}" if fac_id else f"ciwqs-wdid-{wdid}"

    lat = parse_float(rec.get("PLACE LATITUDE"))
    lon = _fix_ca_lon(parse_float(rec.get("PLACE LONGITUDE")))

    # Build programs from facility type info
    programs_parts = []
    prog = _clean(rec.get("PROGRAM"))
    if prog:
        programs_parts.append(prog)
    fac_type = _clean(rec.get("FACILITY TYPE"))
    if fac_type:
        programs_parts.append(fac_type)

    naics = _clean(rec.get("NAICS CODE 1"))
    sic = _clean(rec.get("SIC CODE 1"))

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=_clean(rec.get("FACILITY NAME")) or "Unknown",
        address=_clean(rec.get("PLACE ADDRESS")),
        city=_clean(rec.get("PLACE CITY")),
        state="CA",
        zip_code=_normalize_zip(_clean(rec.get("PLACE ZIP"))),
        county=_clean(rec.get("PLACE COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=naics,
        sic_codes=sic,
        programs=", ".join(programs_parts) if programs_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


def map_ciwqs_enforcement_violation(rec: dict) -> Violation | None:
    """Convert a CIWQS enforcement record to a Violation.

    Key fields: ENFORCEMENT ID (EID), FACILITY ID, AGENCY NAME,
    ENFORCEMENT ACTION TYPE, ENF ACTION EFFECTIVE DATE,
    TOTAL ASSESSMENT AMOUNT, DESCRIPTION.

    CIWQS enforcement records are site-based: FACILITY ID refers to a
    physical discharge point that may have had multiple regulated entities
    (agencies) over time.  The AGENCY NAME field identifies the specific
    respondent named in the enforcement action, which may differ from the
    current facility name.  We always include it in the description so that
    a reviewer can see exactly who the action was issued against.
    """
    eid = _clean(rec.get("ENFORCEMENT ID (EID)"))
    if not eid:
        return None

    fac_id = _clean(rec.get("FACILITY ID"))
    wdid = (_clean(rec.get("WDID")) or "").replace(" ", "") or None
    if fac_id:
        facility_source_id = f"ciwqs-{fac_id}"
    elif wdid:
        facility_source_id = f"ciwqs-wdid-{wdid}"
    else:
        return None

    desc_parts = []
    action_type = _clean(rec.get("ENFORCEMENT ACTION TYPE"))
    if action_type:
        desc_parts.append(action_type)

    # AGENCY NAME is the actual respondent — make it explicit so reviewers
    # are not confused when the respondent differs from the current facility.
    agency_name = _clean(rec.get("AGENCY NAME"))
    if agency_name:
        desc_parts.append(f"Respondent: {agency_name}")

    title = _clean(rec.get("TITLE"))
    if title:
        desc_parts.append(title)
    desc = _clean(rec.get("DESCRIPTION"))
    if desc:
        desc_parts.append(desc)
    amount = parse_float(rec.get("TOTAL ASSESSMENT AMOUNT"))
    if amount and amount > 0:
        desc_parts.append(f"Assessment: ${amount:,.0f}")

    return Violation(
        source=SOURCE,
        source_id=f"ciwqs-e-{eid}",
        facility_source_id=facility_source_id,
        facility_source=SOURCE,
        violation_type=action_type or "Enforcement Action",
        violation_date=_parse_date_mdy(rec.get("ENF ACTION EFFECTIVE DATE")),
        statute=_clean(rec.get("ORDER / RESOLUTION NUMBER")),
        program_area=_clean(rec.get("PROGRAM")) or "Water Quality",
        severity=_enforcement_severity(rec),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# SMARTS Stormwater Violations (84K records) → Facility + Violation
# ---------------------------------------------------------------------------

def map_smarts_facility(rec: dict) -> Facility | None:
    """Convert a SMARTS violation record to a Facility.

    Key fields: WDID, PLACE_NAME, PLACE_ADDRESS, PLACE_CITY, PLACE_STATE,
    PLACE_ZIP, PLACE_COUNTY, PLACE_LATITUDE, PLACE_LONGITUDE.
    """
    wdid = (_clean(rec.get("WDID")) or "").replace(" ", "") or None
    if not wdid:
        return None

    return Facility(
        source=SOURCE,
        source_id=f"smarts-{wdid}",
        name=_clean(rec.get("PLACE_NAME")) or "Unknown",
        address=_clean(rec.get("PLACE_ADDRESS")),
        city=_clean(rec.get("PLACE_CITY")),
        state="CA",
        zip_code=_normalize_zip(_clean(rec.get("PLACE_ZIP"))),
        county=_clean(rec.get("PLACE_COUNTY")),
        lat=parse_float(rec.get("PLACE_LATITUDE")),
        lon=_fix_ca_lon(parse_float(rec.get("PLACE_LONGITUDE"))),
        naics_codes=None,
        sic_codes=None,
        programs=_clean(rec.get("PERMIT_TYPE")) or "Stormwater",
        last_updated=datetime.now(timezone.utc),
    )


def map_smarts_violation(rec: dict) -> Violation:
    """Convert a SMARTS violation record to a Violation.

    Key fields: VIOLATION_ID, WDID, VIOLATION_TYPE, SERIOUS_VIOLATION,
    OCCURRENCE_DATE, DESCRIPTION, VIOLATION_STATUS.
    """
    vid = _clean(rec.get("VIOLATION_ID")) or ""
    wdid = (_clean(rec.get("WDID")) or "").replace(" ", "") or None
    facility_source_id = f"smarts-{wdid}" if wdid else f"smarts-orphan-{vid}"

    desc_parts = []
    vtype = _clean(rec.get("VIOLATION_TYPE"))
    if vtype:
        desc_parts.append(vtype)
    desc = _clean(rec.get("DESCRIPTION"))
    if desc:
        desc_parts.append(desc)
    water = _clean(rec.get("RECEIVING_WATER_NAME"))
    if water:
        desc_parts.append(f"Receiving water: {water}")

    permit_type = _clean(rec.get("PERMIT_TYPE")) or "Stormwater"

    return Violation(
        source=SOURCE,
        source_id=f"smarts-v-{vid}",
        facility_source_id=facility_source_id,
        facility_source=SOURCE,
        violation_type=vtype or "Stormwater Violation",
        violation_date=_parse_date_iso(rec.get("OCCURRENCE_DATE")),
        statute=_clean(rec.get("VIOLATION_SOURCE")),
        program_area=permit_type,
        severity=_smarts_severity(rec),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
