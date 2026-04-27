"""Map raw EPA ECHO RCRA JSON rows to Pydantic models."""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, parse_date, normalize_naics_codes

SOURCE = "epa_rcra"

# EPA ECHO RCRA permit type codes that indicate Treatment, Storage, or Disposal
# (Part B permit holders regulated under 40 CFR Parts 264/265).
# These appear in the RCRAPermitTypes field as comma-separated codes.
_TSDF_PERMIT_CODES = frozenset({
    "TSD",    # Treatment, Storage, and Disposal
    "TSDF",   # Treatment, Storage, Disposal Facility (less common variant)
    "TSS",    # Treatment, Storage (solid waste)
    "HT",     # Hazardous Waste Treatment (some states)
    "TD",     # Treatment and Disposal
    "SD",     # Storage and Disposal
    "TS",     # Treatment and Storage
})


def _parse_date(val: str | None) -> date | None:
    return parse_date(val, formats=("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"))


def _map_programs(row: dict) -> str:
    """Return the program label for an RCRA facility row.

    Distinguishes RCRA TSDF (Part B permit holders) from generators (LQG, SQG,
    VSQG/CESQG) using two EPA ECHO fields:
      - RCRAPermitTypes: comma-separated permit type codes; "TSD" / "TSDF" -> TSDF
      - RCRAGens: generator category ("LQG", "SQG", "CESQG", "VSQG")

    Also preserves Corrective Action (CA) tagging from CIV-438:
      - CleanupActionFlag='Y' -> "RCRA CA"

    Program string values used by scoring.py:
      "RCRA CA"   -> corrective action site (CIV-438 filter, +15 risk pts)
      "RCRA TSDF" -> triggers TSDF score floor (>=60, HIGH risk)
      "RCRA LQG"  -> large-quantity generator
      "RCRA SQG"  -> small-quantity generator
      "RCRA VSQG" -> very small quantity generator
      "RCRA"      -> generic (no handler type available)
    """
    # Corrective Action sites get their own label (CIV-438)
    if row.get("CleanupActionFlag") == "Y":
        return "RCRA CA"

    programs = ["RCRA"]

    # Check for TSDF permit types first (highest risk designation)
    permit_types = clean(row.get("RCRAPermitTypes")) or ""
    codes = [c.strip().upper() for c in permit_types.split(",") if c.strip()]
    if any(c in _TSDF_PERMIT_CODES for c in codes):
        programs.append("RCRA TSDF")
        return ",".join(programs)

    # Check generator status
    gens = clean(row.get("RCRAGens")) or ""
    gens_upper = gens.upper().strip()
    if gens_upper in ("LQG", "LARGE QUANTITY GENERATOR"):
        programs.append("RCRA LQG")
    elif gens_upper in ("SQG", "SMALL QUANTITY GENERATOR"):
        programs.append("RCRA SQG")
    elif gens_upper in ("CESQG", "VSQG", "VERY SMALL QUANTITY GENERATOR",
                        "CONDITIONALLY EXEMPT SMALL QUANTITY GENERATOR"):
        programs.append("RCRA VSQG")

    return ",".join(programs)


def map_facility(row: dict) -> Facility:
    """Convert an RCRA facility JSON row to a Facility model."""
    # Prefer FacName (full name from ECHO) over RCRAName (30-char truncated from RCRA system)
    name = row.get("FacName") or row.get("RCRAName") or "Unknown"
    return Facility(
        source=SOURCE,
        source_id=row.get("RegistryID") or row.get("SourceID", ""),
        name=name,
        address=clean(row.get("RCRAStreet")),
        city=clean(row.get("RCRACity")),
        state=clean(row.get("RCRAState")),
        zip_code=clean(row.get("RCRAZip")),
        county=clean(row.get("RCRACounty")),
        lat=parse_float(row.get("FacLat")),
        lon=parse_float(row.get("FacLong")),
        naics_codes=normalize_naics_codes(clean(row.get("RCRANAICS"))),
        sic_codes=clean(row.get("FacSICCodes")),
        programs=_map_programs(row),
        last_updated=datetime.now(timezone.utc),
    )


def has_violation(row: dict) -> bool:
    """Check whether an RCRA facility row represents an actual violation.

    EPA returns a compliance status for every facility.  ~99.5% are
    "No Violation Identified" which are not real violations.  Only rows with
    actual compliance problems should be ingested as violations.
    """
    status = clean(row.get("RCRAComplStatus")) or ""
    snc = clean(row.get("RCRASNC")) or ""
    viol_types = clean(row.get("RCRAViolationTypes")) or ""

    if status.lower() in ("no violation identified", ""):
        # Only keep if SNC flag is set or there are real violation type codes
        if snc.lower() != "yes":
            # Check violation types — filter out pure "NA"
            codes = [c.strip() for c in viol_types.split(",") if c.strip() and c.strip().upper() != "NA"]
            if not codes:
                return False
    return True


def map_violation(row: dict) -> Violation:
    """Convert an RCRA facility row into a Violation record.

    RCRA endpoint fields: RCRAComplStatus, RCRASNC, RCRAQtrsWithNC,
    RCRAViolationTypes, RCRAOldestOpenVioDate, etc.
    """
    source_id = row.get("SourceID", row.get("RegistryID", ""))
    facility_source_id = row.get("RegistryID") or row.get("SourceID", "")
    compl_status = clean(row.get("RCRAComplStatus"))
    snc_status = clean(row.get("RCRASNC"))
    viol_types = clean(row.get("RCRAViolationTypes"))

    # Build a meaningful description from the violation type codes
    codes = []
    if viol_types:
        codes = [c.strip() for c in viol_types.split(",") if c.strip() and c.strip().upper() != "NA"]
    description = ", ".join(codes) if codes else compl_status

    return Violation(
        source=SOURCE,
        source_id=f"rcra-{source_id}",
        facility_source_id=str(facility_source_id),
        facility_source=SOURCE,
        violation_type=compl_status or "Violation",
        violation_date=_parse_date(
            row.get("RCRAOldestOpenVioDate") or row.get("RCRALastInspectionDate")
        ),
        statute="RCRA",
        program_area="RCRA",
        severity="Significant" if snc_status and snc_status.lower() == "yes" else compl_status,
        description=description,
        last_updated=datetime.now(timezone.utc),
    )
