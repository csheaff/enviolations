"""Map raw TCEQ Socrata CSV rows to Pydantic models.

TCEQ data is downloaded as CSV from data.texas.gov. The csv.DictReader
produces keys matching the CSV column headers (human-readable names),
NOT the Socrata API field names. This mapper handles the CSV headers.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from ..models import Facility, Violation
from ._utils import clean, normalize_naics_codes

SOURCE = "tceq"

_RESOLVED_THRESHOLD_YEARS = 5

# Mapping from TCEQ Violation Citations dataset curr_viol_status codes to
# human-readable display strings.  Used when building the status lookup from
# the gyd4-wuys dataset.
_CITATION_STATUS_MAP: dict[str, str] = {
    "ACTIVE": "Active",
    "IN REVIEW": "In Review",
    "RESOLVED": "Resolved",
    "REFERTOEPA": "Referred to EPA",
    "URESOSCHED": "Pending Resolution",
    "UCOMPSCHED": "Pending Compliance",
}

# ---------------------------------------------------------------------------
# Personal-name / residential-registrant detection
# ---------------------------------------------------------------------------

# Honorifics and generational suffixes that appear in personal names
_HONORIFICS = r"(?:JR|SR|II|III|IV|DR|MR|MRS|MS)"

# Personal name patterns (all-caps, as stored in TCEQ registry):
#   Pattern A: FIRST [MI] LAST — e.g. "SUSAN E DAVIS", "JOSEPH G DOW"
#              Middle word is a single capital letter (initial)
#   Pattern B: LAST [JR|SR|...] FIRST [MI] — e.g. "FULLER JR WILLIAM K"
#              Second token is an honorific/suffix, third+ tokens are first name
#   Pattern C: LAST FIRST — plain two-token "HILL HARRY" style
#              Both tokens are all-caps alphabetic words ≥2 chars, no digits,
#              no business keywords
#
# We require the full name to be all-caps alphabetic (spaces allowed) with no
# digits, punctuation (other than spaces/hyphens), or recognizable business
# keywords, so legitimate business names like "ACME REFINERY" or "HILL COUNTRY
# TIRE" are not flagged.

_PERSONAL_NAME_RE = re.compile(
    r"""
    ^
    (?:
        # Pattern A: FIRST INITIAL LAST  (e.g. "SUSAN E DAVIS")
        [A-Z][A-Z\-]+                   # first name (≥2 alpha chars)
        \s+ [A-Z] \s+                   # single middle initial
        [A-Z][A-Z\-]+                   # last name (≥2 alpha chars)
      |
        # Pattern B: LAST SUFFIX FIRST [INITIAL]  (e.g. "FULLER JR WILLIAM K")
        [A-Z][A-Z\-]+                   # last name
        \s+ """ + _HONORIFICS + r"""    # suffix (JR, SR, II, …)
        \s+ [A-Z][A-Z\-]+               # first name
        (?:\s+ [A-Z])?                  # optional middle initial
    )
    $
    """,
    re.VERBOSE,
)

# Business keywords that should NOT be present in a personal-name candidate.
# If any of these appear as a whole word in the name, the record is a business.
_BUSINESS_KEYWORDS_RE = re.compile(
    r"\b(?:LLC|INC|CORP|CO|LTD|LP|LLP|PARTNERSHIP|COMPANY|INDUSTRIES|"
    r"SERVICES|SOLUTIONS|GROUP|HOLDINGS|ENTERPRISES|ASSOCIATES|CONTRACTORS|"
    r"CONSTRUCTION|MANAGEMENT|RESOURCES|SYSTEMS|TECHNOLOGIES|TRANSPORT|"
    r"TRUCKING|HAULING|DISPOSAL|ENVIRONMENTAL|WASTE|CHEMICAL|REFINERY|"
    r"MANUFACTURING|PROCESSING|ENERGY|OIL|GAS|PETROLEUM|STEEL|METALS|"
    r"FARMS|RANCH|RANCH|PROPERTIES|REALTY|DEVELOPMENT|FOUNDATION|"
    r"CITY|COUNTY|DISTRICT|AUTHORITY|DEPARTMENT|MUNICIPAL|STATE|FEDERAL|"
    r"SCHOOL|HOSPITAL|CLINIC|CENTER|CHURCH|TEMPLE)\b",
    re.IGNORECASE,
)

# Residential address unit designators — presence indicates an apartment/unit
_RESIDENTIAL_UNIT_RE = re.compile(
    r"\b(?:APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM|TRLR|TRAILER|LOT|SP|SPACE|"
    r"PMB|PO\s+BOX|P\.O\.)\b",
    re.IGNORECASE,
)


def is_personal_name(name: str) -> bool:
    """Return True if *name* looks like an individual person's name.

    Uses regex heuristics targeting all-caps TCEQ registry names.  Designed
    to catch patterns like:
      - "SUSAN E DAVIS"     (First MI Last)
      - "JOSEPH G DOW"      (First MI Last)
      - "FULLER JR WILLIAM K" (Last JR/SR First MI)

    Returns False if the name contains business keywords (LLC, INC, etc.),
    digits, or other non-name tokens.
    """
    if not name:
        return False
    name = name.strip()
    # Reject names with digits (street numbers, permit numbers)
    if re.search(r"\d", name):
        return False
    # Reject names with business keywords
    if _BUSINESS_KEYWORDS_RE.search(name):
        return False
    return bool(_PERSONAL_NAME_RE.match(name))


def has_residential_indicator(address: str | None) -> bool:
    """Return True if *address* contains a residential unit designator.

    Matches APT, UNIT, STE, SUITE, TRLR, LOT, etc.  Used as a secondary
    signal alongside :func:`is_personal_name`.
    """
    if not address:
        return False
    return bool(_RESIDENTIAL_UNIT_RE.search(address))


def _derive_status(violation_date: date | None) -> str:
    """Derive violation status from date using a 5-year heuristic.

    Used as a fallback when no citation-level status data is available (e.g.
    for NOEs or NOVs not present in the Violation Citations dataset).
    NOVs and NOEs older than 5 years are treated as Resolved; more recent
    violations are treated as Active.  If no date is available, default to Active.
    """
    if violation_date is None:
        return "Active"
    cutoff = date.today() - timedelta(days=_RESOLVED_THRESHOLD_YEARS * 365)
    return "Resolved" if violation_date < cutoff else "Active"


def aggregate_nov_status(raw_statuses: list[str]) -> str:
    """Aggregate a list of per-citation status codes into a single NOV status.

    Priority order (highest priority wins):
      1. Any ACTIVE citation  → "Active"
      2. Any IN REVIEW        → "In Review"
      3. Any URESOSCHED       → "Pending Resolution"
      4. Any UCOMPSCHED       → "Pending Compliance"
      5. Any REFERTOEPA       → "Referred to EPA"
      6. All RESOLVED         → "Resolved"
      7. Unknown codes        → "Active" (fail safe)
    """
    if not raw_statuses:
        return "Active"
    statuses_upper = [s.upper().strip() for s in raw_statuses]
    if "ACTIVE" in statuses_upper:
        return "Active"
    if "IN REVIEW" in statuses_upper:
        return "In Review"
    if "URESOSCHED" in statuses_upper:
        return "Pending Resolution"
    if "UCOMPSCHED" in statuses_upper:
        return "Pending Compliance"
    if "REFERTOEPA" in statuses_upper:
        return "Referred to EPA"
    if all(s == "RESOLVED" for s in statuses_upper):
        return "Resolved"
    return "Active"


def build_nov_status_lookup(citation_rows: list[dict]) -> dict[str, str]:
    """Build a lookup from Notice of Violation ID to aggregate status.

    *citation_rows* should be the raw CSV rows from the TCEQ Violation Citations
    dataset (dataset ID gyd4-wuys).  The CSV column names are:
      "Notice of Violation ID" and "Current Violation Status".

    Returns a dict mapping NOV ID string to a human-readable status string.
    """
    nov_raw_statuses: dict[str, list[str]] = {}
    for row in citation_rows:
        nov_id = (row.get("Notice of Violation ID") or "").strip()
        status = (row.get("Current Violation Status") or "").strip().upper()
        if nov_id and status:
            nov_raw_statuses.setdefault(nov_id, []).append(status)
    return {
        nov_id: aggregate_nov_status(statuses)
        for nov_id, statuses in nov_raw_statuses.items()
    }


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    val = val.strip()
    # CSV dates: "Mar 31, 2022", "04/14/2022", "2024-01-15T00:00:00.000"
    for fmt in (
        "%b %d, %Y",
        "%m/%d/%Y",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(val, fmt).date()
            # Reject sentinel dates like 3000-12-31
            if parsed.year > 2026:
                return None
            return parsed
        except ValueError:
            continue
    return None


def _extract_naics(row: dict) -> str | None:
    """Extract NAICS code from Central Registry industry type fields."""
    code = clean(row.get("AI NAICS Code"))
    code1 = clean(row.get("RE NAICS Code"))
    codes = [c for c in (code, code1) if c]
    raw = ",".join(codes) if codes else None
    return normalize_naics_codes(raw)


def _extract_sic(row: dict) -> str | None:
    """Extract SIC code from Central Registry row."""
    return clean(row.get("AI SIC Code"))


def _extract_programs(row: dict) -> str | None:
    """Extract program code(s) from Central Registry row."""
    return clean(row.get("Program"))


# Directional corruption corrections — OCR/data-entry errors seen in TCEQ source data.
# Maps corrupted token (whole-word, case-sensitive match) to correct directional.
_DIRECTIONAL_CORRECTIONS: dict[str, str] = {
    "EASE": "EAST",
    "NOTH": "NORTH",
    "WETS": "WEST",
    "SOUT": "SOUTH",
}

_DIRECTIONAL_CORRECTIONS_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _DIRECTIONAL_CORRECTIONS) + r")\b"
)


def _normalize_address(val: str | None) -> str | None:
    """Apply known TCEQ source-data corruption corrections to an address string.

    Fixes directional typos that appear in TCEQ source data, e.g.:
      "7915 EASE ELM STREET" -> "7915 EAST ELM STREET"  (EASE -> EAST)
    Only corrects whole-word matches to avoid false positives on names like
    "PLEASANT" or "NORTHWEST".
    """
    if not val:
        return val
    return _DIRECTIONAL_CORRECTIONS_RE.sub(
        lambda m: _DIRECTIONAL_CORRECTIONS[m.group(0)], val
    )


def map_facility(row: dict) -> Facility:
    """Convert a TCEQ Central Registry CSV row to a Facility model.

    CSV headers: 'Regulated Entity Number (RE)', 'RE Name',
    'RE Physical Address', 'City', 'County', 'State', 'ZIP Code',
    'AI NAICS Code', 'AI SIC Code', 'Program', etc.
    """
    rn = clean(row.get("Regulated Entity Number (RE)")) or ""
    name = clean(row.get("RE Name")) or "Unknown"

    return Facility(
        source=SOURCE,
        source_id=rn,
        name=name,
        address=_normalize_address(clean(row.get("RE Physical Address"))),
        city=clean(row.get("City")),
        state=clean(row.get("State")) or "TX",
        zip_code=clean(row.get("ZIP Code")),
        county=clean(row.get("County")),
        lat=None,
        lon=None,
        naics_codes=_extract_naics(row),
        sic_codes=_extract_sic(row),
        programs=_extract_programs(row),
        last_updated=datetime.now(timezone.utc),
    )


def map_nov(row: dict, nov_status_lookup: dict[str, str] | None = None) -> Violation:
    """Convert a TCEQ Notice of Violation CSV row to a Violation model.

    CSV headers: 'Regulated Entity Number', 'Regulated Entity Name',
    'Investigation Number', 'Investigation Approved Date', 'NOV Date',
    'Notice of Violation ID', 'Cat. A Violation Citations', etc.

    *nov_status_lookup* is an optional dict from :func:`build_nov_status_lookup`
    mapping Notice of Violation ID strings to human-readable status strings.
    When provided, the real citation-level status is used.  When absent (or when
    the NOV ID is not in the lookup), the date-based heuristic is used instead.
    """
    rn = clean(row.get("Regulated Entity Number")) or ""
    violation_id = clean(row.get("Notice of Violation ID")) or clean(
        row.get("Investigation Number")
    ) or ""
    nov_date = _parse_date(row.get("NOV Date")) or _parse_date(
        row.get("Investigation Approved Date")
    )

    # Build severity from violation category citations
    a_cit = clean(row.get("Cat. A Violation Citations"))
    b_cit = clean(row.get("Cat. B Violation Citations"))
    c_cit = clean(row.get("Cat. C Violation Citations"))
    if a_cit:
        severity = "Category A"
    elif b_cit:
        severity = "Category B"
    elif c_cit:
        severity = "Category C"
    else:
        severity = None

    # Combine citation text for description
    parts = []
    if a_cit:
        parts.append(f"Cat A: {a_cit}")
    if b_cit:
        parts.append(f"Cat B: {b_cit}")
    if c_cit:
        parts.append(f"Cat C: {c_cit}")
    description = "; ".join(parts) if parts else None

    # Use real citation status if available; fall back to date heuristic
    if nov_status_lookup is not None and violation_id in nov_status_lookup:
        status = nov_status_lookup[violation_id]
    else:
        status = _derive_status(nov_date)

    return Violation(
        source=SOURCE,
        source_id=f"nov-{violation_id}",
        facility_source_id=rn,
        facility_source=SOURCE,
        violation_type="NOV",
        violation_date=nov_date,
        statute=None,
        program_area=None,
        severity=severity,
        status=status,
        description=description,
        last_updated=datetime.now(timezone.utc),
    )


def map_noe(row: dict) -> Violation:
    """Convert a TCEQ Notice of Enforcement CSV row to a Violation model.

    CSV headers: 'Regulated Entity Number', 'Regulated Entity Name',
    'Investigation Number', 'NOE Date', 'TCEQ Docket Number(s) & Creation Dates',
    'Notice of Enforcement ID', 'Cat. A Violation Citations', etc.
    """
    rn = clean(row.get("Regulated Entity Number")) or ""
    noe_id = clean(row.get("Notice of Enforcement ID")) or clean(
        row.get("Investigation Number")
    ) or ""
    noe_date = _parse_date(row.get("NOE Date"))

    a_cit = clean(row.get("Cat. A Violation Citations"))
    b_cit = clean(row.get("Cat. B Violation Citations"))
    c_cit = clean(row.get("Cat. C Violation Citations"))
    if a_cit:
        severity = "Category A"
    elif b_cit:
        severity = "Category B"
    elif c_cit:
        severity = "Category C"
    else:
        severity = None

    parts = []
    if a_cit:
        parts.append(f"Cat A: {a_cit}")
    if b_cit:
        parts.append(f"Cat B: {b_cit}")
    if c_cit:
        parts.append(f"Cat C: {c_cit}")
    docket = clean(row.get("TCEQ Docket Number(s) & Creation Dates"))
    if docket:
        parts.append(f"Docket: {docket}")
    description = "; ".join(parts) if parts else None

    return Violation(
        source=SOURCE,
        source_id=f"noe-{noe_id}",
        facility_source_id=rn,
        facility_source=SOURCE,
        violation_type="NOE",
        violation_date=noe_date,
        statute=None,
        program_area=None,
        severity=severity,
        status=_derive_status(noe_date),
        description=description,
        last_updated=datetime.now(timezone.utc),
    )
