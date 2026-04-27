"""Mapper for EPA UCMR 5 (Unregulated Contaminant Monitoring Rule) PFAS data.

Aggregates sample-level PFAS monitoring data to one Facility per PWSID
(public water system). Produces Violation records for systems exceeding
EPA MCLs (Maximum Contaminant Levels).

UCMR 5 tests 29 PFAS compounds at ~9,240 public water systems. Data is
bulk tab-delimited text from EPA. Architecture decision: facility-level
aggregation only (Option A per CIV-740). Per-compound concentrations are
Phase II territory, not needed for Phase I ESA screening.

MCL thresholds (EPA final rule, April 2024):
  - PFOA: 4.0 ppt (ng/L)
  - PFOS: 4.0 ppt (ng/L)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean

SOURCE = "epa_ucmr"

# EPA MCL thresholds in ppt (ng/L) for individual PFAS compounds.
# Only PFOA and PFOS have enforceable MCLs as of the April 2024 final rule.
MCL_THRESHOLDS: dict[str, float] = {
    "PFOA": 4.0,
    "PFOS": 4.0,
}


def _safe_float(val) -> float | None:
    """Parse a float, returning None for empty/non-numeric values."""
    if val is None:
        return None
    try:
        v = str(val).strip()
        if not v:
            return None
        f = float(v)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


def aggregate_pws_facilities(
    rows: list[dict],
    pwsid_zips: dict[str, str] | None = None,
) -> list[Facility]:
    """Aggregate sample-level rows into one Facility per PWSID.

    Each row is one sample result (one compound at one sampling point).
    We group by PWSId and aggregate: detected compounds, max concentrations,
    MCL exceedances.

    pwsid_zips: optional PWSID -> ZIP code mapping parsed from UCMR5_ZIPCodes.txt.
    ZIP codes are not present in the main occurrence data file; they must be
    supplied via this lookup.
    """
    if pwsid_zips is None:
        pwsid_zips = {}

    pws_data: dict[str, dict] = {}

    for row in rows:
        pwsid = clean(row.get("PWSId") or row.get("PWSID"))
        if not pwsid:
            continue

        if pwsid not in pws_data:
            # PWSName is in the main data; ZipCode is not — use the lookup.
            # PrimacyAgency is absent from UCMR5_All.txt; State contains the
            # 2-letter code for most states (some use EPA numeric codes, which
            # are handled in the fallback-to-PWSID-prefix logic below).
            pws_data[pwsid] = {
                "name": clean(row.get("PWSName") or row.get("FacilityName")) or "Unknown",
                "state": clean(row.get("PrimacyAgency") or row.get("State")),
                "zip_code": pwsid_zips.get(pwsid),
                "detected_compounds": set(),
                "mcl_exceedances": set(),
                "max_concentrations": {},
                "sample_count": 0,
            }

        info = pws_data[pwsid]
        info["sample_count"] += 1

        contaminant = clean(row.get("Contaminant") or row.get("AnalyteName"))
        result_val = _safe_float(row.get("AnalyticalResultValue") or row.get("AnalyticalResultsValue"))

        if contaminant and result_val is not None and result_val > 0:
            info["detected_compounds"].add(contaminant)

            # Track max concentration per compound
            current_max = info["max_concentrations"].get(contaminant, 0.0)
            if result_val > current_max:
                info["max_concentrations"][contaminant] = result_val

            # Check MCL exceedance
            mcl = MCL_THRESHOLDS.get(contaminant.upper())
            if mcl is not None and result_val > mcl:
                info["mcl_exceedances"].add(contaminant)

    facilities = []
    now = datetime.now(timezone.utc)

    for pwsid, info in pws_data.items():
        # State code: PrimacyAgency is typically the 2-letter state code
        state = info["state"]
        if state and len(state) > 2:
            state = state[:2]

        # Fallback: extract state from PWSID prefix (e.g., "NJ0100001" -> "NJ")
        if not state and len(pwsid) >= 2:
            prefix = pwsid[:2].upper()
            if prefix.isalpha():
                state = prefix

        programs = ["PFAS Monitoring", "UCMR 5"]
        if info["mcl_exceedances"]:
            programs.append("PFAS MCL Exceedance")

        facilities.append(Facility(
            source=SOURCE,
            source_id=pwsid,
            name=info["name"],
            address=None,
            city=None,
            state=state,
            zip_code=info["zip_code"],
            county=None,
            lat=None,
            lon=None,
            naics_codes="221310",  # Water Supply and Irrigation Systems (public water systems)
            sic_codes=None,
            programs=", ".join(programs),
            last_updated=now,
        ))

    return facilities


def aggregate_pws_violations(rows: list[dict]) -> list[Violation]:
    """Generate Violation records for water systems exceeding PFAS MCLs.

    One violation per (PWSID, compound) pair that exceeds the MCL threshold.
    """
    # Group by PWSID + compound, track max concentration and latest date
    exceedances: dict[tuple[str, str], dict] = {}

    for row in rows:
        pwsid = clean(row.get("PWSId") or row.get("PWSID"))
        if not pwsid:
            continue

        contaminant = clean(row.get("Contaminant") or row.get("AnalyteName"))
        if not contaminant:
            continue

        result_val = _safe_float(row.get("AnalyticalResultValue") or row.get("AnalyticalResultsValue"))
        if result_val is None:
            continue

        mcl = MCL_THRESHOLDS.get(contaminant.upper())
        if mcl is None or result_val <= mcl:
            continue

        key = (pwsid, contaminant)
        sample_date = clean(row.get("SampleCollectionDate") or row.get("CollectionDate"))

        if key not in exceedances:
            exceedances[key] = {
                "max_value": result_val,
                "latest_date": sample_date,
                "pws_name": clean(row.get("PWSName") or row.get("FacilityName")) or "Unknown",
            }
        else:
            info = exceedances[key]
            if result_val > info["max_value"]:
                info["max_value"] = result_val
            if sample_date and (info["latest_date"] is None or sample_date > info["latest_date"]):
                info["latest_date"] = sample_date

    violations = []
    now = datetime.now(timezone.utc)

    for (pwsid, contaminant), info in exceedances.items():
        mcl = MCL_THRESHOLDS[contaminant.upper()]

        viol_date = None
        if info["latest_date"]:
            try:
                viol_date = datetime.strptime(info["latest_date"], "%m/%d/%Y").date()
            except (ValueError, TypeError):
                try:
                    viol_date = datetime.fromisoformat(
                        info["latest_date"].replace("Z", "+00:00")
                    ).date()
                except (ValueError, TypeError):
                    pass

        violations.append(Violation(
            source=SOURCE,
            source_id=f"ucmr-{pwsid}-{contaminant.upper()}",
            facility_source_id=pwsid,
            facility_source=SOURCE,
            violation_type="PFAS MCL Exceedance",
            violation_date=viol_date,
            statute="SDWA",
            program_area="PFAS Monitoring",
            severity="MCL Exceedance",
            description=f"{contaminant} detected at {info['max_value']:.1f} ppt (MCL: {mcl:.1f} ppt)",
            last_updated=now,
        ))

    return violations
