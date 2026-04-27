"""Map raw EPA ECHO JSON rows to Pydantic models."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, parse_date, normalize_naics_codes

SOURCE = "epa_echo"


def _parse_date(val: str | None) -> date | None:
    return parse_date(val, formats=("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"))


# EPA administrative placeholders that are not geographic county names.
# These appear in upstream ECHO data and should be treated as missing.
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


# SIC codes that definitionally indicate underground storage tank operations.
# Facilities in these industries are regulated under EPA OUST (Office of
# Underground Storage Tanks) and should appear in LUST/UST filter searches.
# The EPA ECHO API does not return a dedicated UST flag, so we infer from SIC.
_UST_SIC_CODES = frozenset({"5541", "5171", "5172"})
# SIC code 5541 = Gasoline Stations (incl. convenience stores)
# SIC code 5171 = Petroleum Bulk Stations and Terminals
# SIC code 5172 = Petroleum and Petroleum Products Wholesalers


def _has_ust_sic(sic_codes: str | None) -> bool:
    """Return True if any SIC code indicates underground storage tank operations."""
    if not sic_codes:
        return False
    return any(code in _UST_SIC_CODES for code in sic_codes.split())


def _programs_from_flags(row: dict) -> str | None:
    """Build a comma-separated program list from flag fields."""
    programs = []
    if row.get("AIRFlag") == "Y":
        programs.append("CAA")
    if row.get("CWAComplianceStatus") is not None:
        programs.append("CWA")
    if row.get("RCRAComplianceStatus") is not None:
        programs.append("RCRA")
    if row.get("SDWAComplianceStatus") is not None:
        programs.append("SDWA")
    if row.get("TRIFlag") == "Y":
        programs.append("TRI")
    if _has_ust_sic(row.get("FacSICCodes")):
        programs.append("UST")
    return ",".join(programs) if programs else None


_NO_ADDRESS_SENTINEL = "NO ADDRESS ON FILE"

# NJ municipality type suffix regex — mirrors nj_dep_mapper._NJ_MUNIC_SUFFIX_RE.
# EPA ECHO occasionally stores NJ city names with municipality-type suffixes
# (e.g. "Hoboken City" instead of "Hoboken"). We strip these at ingest time
# for NJ facilities so entity resolution and display work correctly.
_NJ_ECHO_CITY_SUFFIX_RE = re.compile(
    r"\s+(city|town|township|borough|village|boro|twp)$",
    re.IGNORECASE,
)

# NJ city names whose "City" or "Town" is part of the real name (not a suffix).
# Without this guard, "Jersey City" → "Jersey", "Atlantic City" → "Atlantic".
_NJ_ECHO_PROTECTED_CITIES: frozenset[str] = frozenset({
    "jersey city",
    "atlantic city",
    "ocean city",
    "egg harbor city",
    "estell manor city",
})


def _normalize_echo_nj_city(city: str | None) -> str | None:
    """Strip NJ municipality type suffixes from EPA ECHO city names for NJ facilities.

    EPA ECHO occasionally stores NJ city names with municipality-type suffixes
    (e.g. "Hoboken City" → "Hoboken"). Protected names like "Jersey City" and
    "Atlantic City" are exempt from stripping.
    """
    if not city:
        return city
    if city.lower() in _NJ_ECHO_PROTECTED_CITIES:
        return city
    stripped = _NJ_ECHO_CITY_SUFFIX_RE.sub("", city)
    return stripped if stripped else city


def map_facility(row: dict) -> Facility:
    """Convert an ECHO facility JSON row to a Facility model."""
    raw_street = row.get("FacStreet")
    address = clean(raw_street)
    state = clean(row.get("FacState"))

    # Null out coordinates when EPA has no real address on file.
    # These facilities receive a default/fallback coordinate from ECHO
    # (often a generic point like a city centroid or HQ address) that
    # causes them to appear in unrelated geographic radius searches.
    no_address = (raw_street or "").strip().upper() == _NO_ADDRESS_SENTINEL
    lat = None if no_address else parse_float(row.get("FacLat"))
    lon = None if no_address else parse_float(row.get("FacLong"))

    raw_city = clean(row.get("FacCity"))
    city = _normalize_echo_nj_city(raw_city) if state == "NJ" else raw_city

    return Facility(
        source=SOURCE,
        source_id=row.get("RegistryID", ""),
        name=row.get("FacName", "Unknown"),
        address=address,
        city=city,
        state=state,
        zip_code=clean(row.get("FacZip")),
        county=_clean_county(row.get("FacCounty")),
        lat=lat,
        lon=lon,
        naics_codes=normalize_naics_codes(clean(row.get("FacNAICSCodes"))),
        sic_codes=clean(row.get("FacSICCodes")),
        programs=_programs_from_flags(row),
        last_updated=datetime.now(timezone.utc),
    )


def has_violation(row: dict) -> bool:
    """Check whether a CWA facility row represents an actual violation.

    ~90% of CWA records have SNC status "No" and ViolStatus None — these
    are clean compliance checks, not violations.

    Checks three signals:
    - CWPSNCStatus: Significant Non-Complier flag (12-quarter threshold)
    - CWPViolStatus: Current violation status ("Yes" = in violation)
    - CWPe90Cnt: Effluent violations in past 90 days (DMR exceedances not
      yet at SNC threshold — catches active violations before they escalate
      to formal SNC status, e.g. major refineries with recent DMR data)
    """
    snc = clean(row.get("CWPSNCStatus")) or ""
    viol = clean(row.get("CWPViolStatus")) or ""
    # Keep if SNC flag is set to anything meaningful
    if snc.lower() not in ("", "no"):
        return True
    # Keep if violation status indicates a real violation
    if viol and viol.lower() not in ("", "no"):
        return True
    # Keep if there are recent effluent violations (DMR exceedances in past 90 days)
    # that haven't yet escalated to formal SNC status
    e90 = clean(row.get("CWPe90Cnt")) or "0"
    try:
        if int(e90) > 0:
            return True
    except (ValueError, TypeError):
        pass
    return False


def map_violation(row: dict) -> Violation:
    """Convert a CWA facility row into a Violation record.

    CWA endpoint fields: CWPName, SourceID, RegistryID, CWPSNCStatus,
    CWPViolStatus, CWPE90Cnt, CWPComplianceTracking, etc.
    """
    source_id = row.get("SourceID", row.get("RegistryID", ""))
    facility_source_id = row.get("RegistryID") or row.get("SourceID", "")
    snc_status = clean(row.get("CWPSNCStatus"))
    viol_status = clean(row.get("CWPViolStatus"))

    # Build meaningful violation type
    snc_lower = (snc_status or "").lower()
    viol_lower = (viol_status or "").lower()
    # Normalize raw boolean flags from ECHO ("Yes"/"Y") to human-readable labels
    # Used only for the status field — not embedded in violation_type.
    viol_label = "Active Violation" if viol_lower in ("yes", "y") else viol_status
    # viol_is_boolean: True when CWPViolStatus is a raw yes/y flag with no descriptive text.
    # In this case the violation type is just "CWA Violation" — the status field
    # captures "Active Violation". Only embed non-boolean text in the type so that
    # Type and Status columns show distinct information (CIV-764).
    viol_is_boolean = viol_lower in ("yes", "y")
    if snc_lower in ("yes", "y"):
        vtype = "CWA Significant Non-Compliance"
    elif snc_lower and snc_lower not in ("no", ""):
        vtype = f"CWA SNC: {snc_status}"
    elif viol_label and viol_lower not in ("no", "") and not viol_is_boolean:
        vtype = f"CWA Violation: {viol_label}"
    else:
        vtype = "CWA Violation"

    # Build severity
    severity = "Significant" if snc_lower in ("yes", "y") else snc_status

    # Build status: derive a human-readable compliance status from available fields.
    # The CWA aggregate endpoint does not return a dedicated violation-level status
    # field, so we synthesize one from SNC and ViolStatus flags.
    if snc_lower in ("yes", "y"):
        status = "Significant Non-Complier"
    elif snc_lower and snc_lower not in ("no", ""):
        status = f"SNC: {snc_status}"
    elif viol_label and viol_lower not in ("no", ""):
        status = viol_label
    else:
        status = None

    # Build description from available fields
    desc_parts = []
    e90 = clean(row.get("CWPe90Cnt"))
    if e90 and e90 != "0":
        desc_parts.append(f"{e90} effluent violations (90d)")
    tracking = clean(row.get("CWPComplianceTracking"))
    if tracking and tracking.lower() not in ("on", "off", "yes", "no"):
        desc_parts.append(tracking)
    desc = "; ".join(desc_parts) if desc_parts else None

    return Violation(
        source=SOURCE,
        source_id=f"cwa-{source_id}",
        facility_source_id=str(facility_source_id),
        facility_source=SOURCE,
        violation_type=vtype,
        violation_date=_parse_date(row.get("CWPDateLastInspSt") or row.get("CWPIssueDate")),
        statute="CWA",
        program_area="CWA",
        severity=severity,
        status=status,
        description=desc,
        last_updated=datetime.now(timezone.utc),
    )
