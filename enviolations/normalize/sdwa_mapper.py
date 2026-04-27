"""Map raw EPA ECHO SDWA (Safe Drinking Water Act) JSON rows to Pydantic models."""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_date

SOURCE = "epa_sdwa"


def _parse_date(val: str | None) -> date | None:
    return parse_date(val, formats=("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"))


def map_facility(row: dict) -> Facility:
    """Convert an SDWA water system JSON row to a Facility model.

    SDWA has no lat/lon, no street address, and uses PWSId as the primary ID.
    CitiesServed, CountiesServed, ZipCodesServed are comma-separated lists.
    """
    return Facility(
        source=SOURCE,
        source_id=row.get("RegistryID") or row.get("PWSId", ""),
        name=row.get("PWSName", "Unknown"),
        address=None,
        city=clean(row.get("CitiesServed")),
        state=clean(row.get("StateCode")),
        zip_code=clean(row.get("ZipCodesServed")),
        county=clean(row.get("CountiesServed")),
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs="SDWA",
        last_updated=datetime.now(timezone.utc),
    )


def has_violation(row: dict) -> bool:
    """Check whether an SDWA water system row has an actual violation.

    ~83% of SDWA records have SNC/severity "No Violation".  Only keep
    records that are "In Violation" or "SNC" (Significant Noncomplier).
    """
    snc = clean(row.get("SNC")) or ""
    serious = clean(row.get("SeriousViolator")) or ""
    viol_cats = clean(row.get("ViolationCategories")) or ""

    if snc.lower() in ("no violation", ""):
        # Check for serious violator flag
        if serious.lower() != "yes":
            # Check if violation categories exist (e.g., "Monitoring Violation")
            if not viol_cats or viol_cats.lower() == "no violation":
                return False
    return True


def map_violation(row: dict) -> Violation:
    """Convert an SDWA water system row into a Violation record.

    SDWA fields: SNC, SeriousViolator, QtrsWithVio, ViolationCategories,
    SDWAContaminantsInCurViol, SDWDateLastFea, etc.
    """
    pws_id = row.get("PWSId", row.get("RegistryID", ""))
    registry_id = row.get("RegistryID", pws_id)
    snc = clean(row.get("SNC"))
    serious = clean(row.get("SeriousViolator"))
    viol_cats = clean(row.get("ViolationCategories"))

    return Violation(
        source=SOURCE,
        source_id=f"sdwa-{pws_id}",
        facility_source_id=str(registry_id),
        facility_source=SOURCE,
        violation_type=viol_cats or snc,
        violation_date=_parse_date(
            row.get("SDWDateLastFea") or row.get("SDWDateLastIea")
        ),
        statute="SDWA",
        program_area="SDWA",
        severity="SNC" if serious == "Yes" else snc,
        description=clean(
            row.get("SDWAContaminantsInCurViol") or row.get("SDWAContaminantsInViol3yr")
        ),
        last_updated=datetime.now(timezone.utc),
    )
