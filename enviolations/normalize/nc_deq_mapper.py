"""Map raw NC DEQ ArcGIS feature attributes to Pydantic models.

NC DEQ data comes from ArcGIS REST FeatureServer hosted on ArcGIS Online.
Datasets:
  - HW_Sites/FeatureServer/0: Hazardous Waste Sites → Facility
  - Underground_Storage_Tank_Incidents/FeatureServer/0 → Facility + Violation (45K)
  - AST_Incidents/FeatureServer/0 → Facility + Violation (9K)
  - Report_an_SSO_Public_View/FeatureServer/0 → Facility + Violation (3.2K)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, epoch_ms_to_date

SOURCE = "nc_deq"


def _build_programs(attrs: dict) -> str | None:
    """Build programs string from generator/transporter/TSD flags."""
    parts = []
    if clean(attrs.get("GENERATOR")):
        parts.append(f"Generator: {attrs['GENERATOR']}")
    if clean(attrs.get("TRANSPORTER")):
        parts.append("Transporter")
    if clean(attrs.get("TSD")):
        parts.append("TSD")
    if clean(attrs.get("RECYCLER")):
        parts.append("Recycler")
    return "; ".join(parts) if parts else None


def map_hw_site(attrs: dict) -> Facility:
    """Convert a Hazardous Waste Sites feature to a Facility model.

    Key fields: HANDLER_ID, SITE_NAME, LOC_STR_NO, LOC_ADDR_1, LOC_ADDR_2,
    LOC_CITY, LOC_COUNTY, LOC_ZIP, LAT, LONG, GENERATOR, TRANSPORTER, TSD.
    """
    handler_id = clean(attrs.get("HANDLER_ID")) or ""

    addr_parts = []
    str_no = clean(attrs.get("LOC_STR_NO"))
    addr1 = clean(attrs.get("LOC_ADDR_1"))
    if str_no:
        addr_parts.append(str_no)
    if addr1:
        addr_parts.append(addr1)
    address = " ".join(addr_parts) if addr_parts else None

    return Facility(
        source=SOURCE,
        source_id=f"hw-{handler_id}",
        name=clean(attrs.get("SITE_NAME")) or "Unknown",
        address=address,
        city=clean(attrs.get("LOC_CITY")),
        state="NC",
        zip_code=clean(attrs.get("LOC_ZIP")),
        county=clean(attrs.get("LOC_COUNTY")),
        lat=parse_float(attrs.get("LAT")),
        lon=parse_float(attrs.get("LONG")),
        naics_codes=None,
        sic_codes=None,
        programs=_build_programs(attrs),
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# UST/AST Incidents (ArcGIS Online FeatureServer)
# ---------------------------------------------------------------------------

def _tank_severity(risk: str | None) -> str | None:
    """Derive severity from NC DEQ risk classification."""
    if not risk:
        return None
    risk_lower = risk.strip().lower()
    if risk_lower == "high":
        return "High"
    if risk_lower in ("intermediate", "medium"):
        return "Medium"
    if risk_lower == "low":
        return "Low"
    return None


def _map_tank_status(raw: str | None) -> str | None:
    """Normalize NC DEQ CurrStatus codes to human-readable labels.

    The ArcGIS API returns single-character codes:
      - "C" / "c" -> "Closed"
      - "A" / "a" -> "Active"

    Full-word values (e.g. "Closed", "Open", "Active") are also accepted so
    the mapper works correctly with both the live API and test fixtures.
    """
    if not raw:
        return None
    s = raw.strip()
    s_upper = s.upper()
    if s_upper == "C" or s.lower() == "closed":
        return "Closed"
    if s_upper == "A" or s.lower() in ("active", "open"):
        return "Active"
    # Unknown code -- pass through title-cased so it is readable
    return s.title()


def map_tank_incident_facility(attrs: dict, prefix: str) -> Facility:
    """Convert a UST/AST Incident feature to a Facility.

    Fields: IncidentNumber, IncidentName, Address, CityTown, County,
    ZipCode, LatDec, LongDec.
    """
    incident_num = clean(attrs.get("IncidentNumber")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"{prefix}-{incident_num}",
        name=clean(attrs.get("IncidentName")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("CityTown")),
        state="NC",
        zip_code=clean(attrs.get("ZipCode")),
        county=clean(attrs.get("County")),
        lat=parse_float(attrs.get("LatDec")),
        lon=parse_float(attrs.get("LongDec")),
        naics_codes=None,
        sic_codes=None,
        programs=f"{prefix.upper()} Incident",
        last_updated=datetime.now(timezone.utc),
    )


def map_tank_incident_violation(attrs: dict, prefix: str) -> Violation:
    """Convert a UST/AST Incident feature to a Violation.

    Fields: IncidentNumber, IncidentName, CurrStatus, Risk, ConfRisk,
    DateOccurred.
    """
    incident_num = clean(attrs.get("IncidentNumber")) or ""
    name = clean(attrs.get("IncidentName")) or ""
    mapped_status = _map_tank_status(clean(attrs.get("CurrStatus")))

    desc_parts = []
    if name:
        desc_parts.append(name)
    if mapped_status:
        desc_parts.append(f"Status: {mapped_status}")

    return Violation(
        source=SOURCE,
        source_id=f"{prefix}-{incident_num}",
        facility_source_id=f"{prefix}-{incident_num}",
        facility_source=SOURCE,
        violation_type=f"{prefix.upper()} Incident",
        violation_date=epoch_ms_to_date(attrs.get("DateOccurred")),
        statute=None,
        program_area="Storage Tanks",
        severity=_tank_severity(attrs.get("Risk") or attrs.get("ConfRisk")),
        status=mapped_status,
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# SSO Reports (ArcGIS Online FeatureServer)
# ---------------------------------------------------------------------------

def _sso_severity(attrs: dict) -> str:
    """Derive severity from SSO report fields."""
    reaches_water = attrs.get("reachSurfaceWater")
    if reaches_water in (True, "Yes", "yes", 1, "1"):
        return "High"
    return "Medium"


def map_sso_facility(attrs: dict) -> Facility:
    """Convert an SSO Report feature to a Facility.

    Fields: OBJECTID, permitID, facName, permCoName.
    """
    obj_id = attrs.get("OBJECTID") or attrs.get("ObjectId") or ""

    return Facility(
        source=SOURCE,
        source_id=f"sso-{obj_id}",
        name=clean(attrs.get("facName")) or clean(attrs.get("permCoName")) or "Unknown",
        address=None,
        city=None,
        state="NC",
        zip_code=None,
        county=None,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs="SSO Report",
        last_updated=datetime.now(timezone.utc),
    )


def map_sso_violation(attrs: dict) -> Violation:
    """Convert an SSO Report feature to a Violation.

    Fields: OBJECTID, permitID, facName, rep_Date, DWR_IncVolume,
    reachSurfaceWater, incCause, waterbody_label.
    """
    obj_id = attrs.get("OBJECTID") or attrs.get("ObjectId") or ""
    source_id = f"sso-{obj_id}"

    desc_parts = []
    fac_name = clean(attrs.get("facName"))
    if fac_name:
        desc_parts.append(fac_name)
    cause = clean(attrs.get("incCause"))
    if cause:
        desc_parts.append(f"Cause: {cause}")
    waterbody = clean(attrs.get("waterbody_label"))
    if waterbody:
        desc_parts.append(f"Waterbody: {waterbody}")
    volume = parse_float(attrs.get("DWR_IncVolume"))
    if volume is not None:
        desc_parts.append(f"Volume: {volume:.0f} gal")

    return Violation(
        source=SOURCE,
        source_id=source_id,
        facility_source_id=source_id,
        facility_source=SOURCE,
        violation_type="SSO",
        violation_date=epoch_ms_to_date(attrs.get("rep_Date")),
        statute=None,
        program_area="Wastewater",
        severity=_sso_severity(attrs),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
