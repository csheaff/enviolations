"""Map raw EPA ECHO CAA (Clean Air Act) JSON rows to Pydantic models."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, parse_date

SOURCE = "epa_caa"

# Regex to detect unit tokens that appear mid-address (before street type/directional)
_UNIT_MID_RE = re.compile(
    r"(#\s*\d+|STE\.?\s+\d+|SUITE\.?\s+\d+|APT\.?\s+\d+|UNIT\s+\d+)",
    re.IGNORECASE,
)


def _fix_mid_unit(s: str) -> str:
    """Move a unit token to the end if street content follows it.

    Example: '1720 LOUISIANA #100 BLVD NE' → '1720 LOUISIANA BLVD NE #100'
    """
    m = _UNIT_MID_RE.search(s)
    if not m:
        return s
    unit_text = m.group(0).strip()
    before = s[: m.start()].strip()
    after = s[m.end() :].strip()
    if after:
        return f"{before} {after} {unit_text}".strip()
    return s


def _clean_address(val: str | None) -> str | None:
    """Validate and normalize a CAA street address string.

    Three failure modes handled:

    1. **No leading street number** — strings like 'PORTABLE CONCRETE BATCH PLANT'
       contain no leading digits and are equipment descriptions, not addresses.
       Returns None so the display layer shows 'Address not available'.

    2. **Unit token mid-address** — '1720 LOUISIANA #100 BLVD NE' has the unit
       token between the street name and street type.  We move it to the end:
       '1720 LOUISIANA BLVD NE #100'.

    3. **Unparseable corruption** — if scourgify raises UnParseableAddressError,
       the string cannot be interpreted as a street address; return None.
       Strings that are garbled but still parseable (e.g. '2703 SMA AMTEO NE')
       pass through unchanged — no reliable heuristic can detect character-
       substitution corruption without an address dictionary.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None

    # 1. Must start with a digit to be a street address
    if not re.match(r"^\d", s):
        return None

    # 2. Reorder mid-address unit tokens to end
    s = _fix_mid_unit(s)

    # 3. Scourgify validation — reject completely unparseable strings
    try:
        from scourgify import normalize_address_record
        from scourgify.exceptions import UnParseableAddressError

        try:
            normalize_address_record(s)
        except UnParseableAddressError:
            return None
    except ImportError:
        pass  # scourgify not available; skip validation step

    return s


def _parse_date(val: str | None) -> date | None:
    return parse_date(val, formats=("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"))


# SIC codes that indicate underground storage tank operations.
# Facilities in these industries appear in LUST/UST filter searches.
# Mirrors echo_mapper._UST_SIC_CODES — gas stations regulated under EPA OUST.
_UST_SIC_CODES = frozenset({"5541", "5171", "5172"})
# SIC 5541 = Gasoline Stations, SIC 5171 = Petroleum Bulk Stations,
# SIC 5172 = Petroleum and Petroleum Products Wholesalers


def _has_ust_sic(sic_codes: str | None) -> bool:
    """Return True if any SIC code indicates underground storage tank operations."""
    if not sic_codes:
        return False
    return any(code in _UST_SIC_CODES for code in sic_codes.split())


# EPA administrative placeholders that are not geographic county names.
_NON_COUNTY_VALUES = frozenset({
    "PORTABLE SOURCE",
    "-- NOT DEFINED --",
    "NOT DEFINED",
    "UNKNOWN",
    "N/A",
})


def _clean_county(val: str | None) -> str | None:
    """Return None for known non-geographic EPA county placeholders."""
    cleaned = clean(val)
    if cleaned is None:
        return None
    if cleaned.upper() in _NON_COUNTY_VALUES:
        return None
    return cleaned


def map_facility(row: dict) -> Facility:
    """Convert a CAA facility JSON row to a Facility model."""
    sic_codes = clean(row.get("FacSICCodes"))
    programs = ["CAA"]
    if _has_ust_sic(sic_codes):
        programs.append("UST")
    return Facility(
        source=SOURCE,
        source_id=row.get("RegistryID") or row.get("SourceID", ""),
        name=row.get("AIRName", "Unknown"),
        address=_clean_address(clean(row.get("AIRStreet"))),
        city=clean(row.get("AIRCity")),
        state=clean(row.get("AIRState")),
        zip_code=clean(row.get("AIRZip")),
        county=_clean_county(row.get("AIRCounty")),
        lat=parse_float(row.get("FacLat")),
        lon=parse_float(row.get("FacLong")),
        naics_codes=clean(row.get("AIRNAICS")),
        sic_codes=sic_codes,
        programs=",".join(programs),
        last_updated=datetime.now(timezone.utc),
    )


def has_violation(row: dict) -> bool:
    """Check whether a CAA facility row represents an actual violation.

    ~99.5% of CAA records are "No High Priority Violation" compliance checks,
    not actual violations.  Only rows with real HPV status or violation quarters
    should be ingested.
    """
    hpv = clean(row.get("AIRHpvStatus")) or ""
    if hpv.lower() in ("no high priority violation", ""):
        # Check if there are actual violation quarters
        qtrs = clean(row.get("AIRQtrsWithViol")) or "0"
        try:
            if int(qtrs) == 0:
                return False
        except ValueError:
            return False
    return True


# EPA ECHO AIRPollRecentViol internal codes that are not pollutant names.
# These appear in source data as the "most recent pollutant/violation type" and
# are EPA administrative classifications rather than specific pollutant names.
_AIR_POLL_CODE_MAP: dict[str, str] = {
    "FACIL": "Facility-level compliance flag",
    "MULTI": "Multiple pollutants",
    "OTHER": "Other violation type",
}

# Maps AIRHpvStatus codes to human-readable compliance status strings.
# HPV = High Priority Violation — this is the EPA CAA enforcement status.
_HPV_STATUS_MAP: dict[str, str] = {
    "Unaddressed-State": "Unaddressed (State)",
    "Unaddressed-EPA": "Unaddressed (EPA)",
    "Addressed-State": "Addressed (State)",
    "Addressed-EPA": "Addressed (EPA)",
    "No High Priority Violation": "No Violation",
}


def _map_poll_description(raw: str | None) -> str | None:
    """Map a raw AIRPollRecentViol value to a human-readable description.

    Translates EPA internal codes (e.g. "FACIL") to plain language.
    Passes through pollutant names (e.g. "PM2.5", "CO") unchanged.
    """
    if not raw:
        return None
    val = raw.strip()
    return _AIR_POLL_CODE_MAP.get(val.upper(), val) or None


def _hpv_status_display(hpv_status: str | None) -> str | None:
    """Return a human-readable compliance status from an HPV status code.

    Maps known EPA HPV status codes to display strings.  Passes unknown
    values through unchanged so new codes from EPA don't silently vanish.
    """
    if not hpv_status:
        return None
    return _HPV_STATUS_MAP.get(hpv_status, hpv_status)


def map_violation(row: dict) -> Violation:
    """Convert a CAA facility row into a Violation record.

    CAA endpoint fields: AIRComplStatus, AIRHpvStatus, AIRQtrsWithViol,
    AIRLastViolDate, AIRPollRecentViol, etc.
    """
    source_id = row.get("SourceID", row.get("RegistryID", ""))
    facility_source_id = row.get("RegistryID") or row.get("SourceID", "")
    compl_status = clean(row.get("AIRComplStatus"))
    hpv_status = clean(row.get("AIRHpvStatus"))

    return Violation(
        source=SOURCE,
        source_id=f"caa-{source_id}",
        facility_source_id=str(facility_source_id),
        facility_source=SOURCE,
        violation_type=hpv_status or compl_status,
        violation_date=_parse_date(row.get("AIRLastViolDate")),
        statute="CAA",
        program_area="CAA",
        severity=hpv_status,
        status=_hpv_status_display(hpv_status),
        description=_map_poll_description(row.get("AIRPollRecentViol")),
        last_updated=datetime.now(timezone.utc),
    )
