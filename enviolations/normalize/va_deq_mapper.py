"""Map raw Virginia DEQ ArcGIS feature data to Pydantic models.

Virginia DEQ data comes from the EDMA ArcGIS MapServer as JSON features.
Three facility datasets:
  - Active Air Sites (Layer 294) → Facility (geometry provides lat/lon)
  - Solid Waste Permits (Layer 100) → Facility (geometry provides lat/lon)
  - Petroleum Tank Facilities (Layer 102) → Facility (explicit Lat/Lon fields)

Two violation datasets:
  - Petroleum Releases (Layer 104) → Violation + Facility (53K confirmed releases)
  - PReP Reports (Layer 175) → Violation + Facility (28K pollution incidents)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, extract_arcgis_coords, epoch_ms_to_date

SOURCE = "va_deq"


def map_air_site(feature: dict) -> Facility:
    """Convert Active Air Sites feature to a Facility model.

    Key attribute fields: PLA_ID, PLA_REG_NUM, PLA_NAME, FAC_L_ADDR_1,
    FAC_L_CITY, FAC_L_STATE, FAC_L_ZIP5, PLA_NAC_CODE_PRIMARY,
    PLA_ICIS_ID, PLA_DESC, PCL_STATE_DESC, AIR_OP_STATUS, PCL_FED_DESC.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    pla_id = attrs.get("PLA_ID")
    pla_id_str = str(int(pla_id)) if pla_id is not None else ""

    programs = []
    state_desc = clean(attrs.get("PCL_STATE_DESC"))
    fed_desc = clean(attrs.get("PCL_FED_DESC"))
    if state_desc:
        programs.append(state_desc)
    if fed_desc:
        programs.append(fed_desc)

    return Facility(
        source=SOURCE,
        source_id=f"air-{pla_id_str}",
        name=clean(attrs.get("PLA_NAME")) or "Unknown",
        address=clean(attrs.get("FAC_L_ADDR_1")),
        city=clean(attrs.get("FAC_L_CITY")),
        state=clean(attrs.get("FAC_L_STATE")) or "VA",
        zip_code=clean(attrs.get("FAC_L_ZIP5")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=clean(attrs.get("PLA_NAC_CODE_PRIMARY")),
        sic_codes=None,
        programs=", ".join(programs) if programs else "Air",
        last_updated=datetime.now(timezone.utc),
    )


def map_solid_waste(feature: dict) -> Facility:
    """Convert Solid Waste Permits feature to a Facility model.

    Key attribute fields: PMT_ID, FACILITY_NAME, FACILITY_ADDRESS,
    FACILITY_CITY, FACILITY_STATE, FACILITY_ZIP5, FACILITY_COUNTY,
    PERMIT_TYPE, PERMIT_STATUS, OPERATING_STATUS, SITE_NAME.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    pmt_id = clean(attrs.get("PMT_ID")) or ""

    programs = []
    permit_type = clean(attrs.get("PERMIT_TYPE"))
    permit_status = clean(attrs.get("PERMIT_STATUS"))
    if permit_type:
        programs.append(permit_type)
    if permit_status:
        programs.append(permit_status)

    return Facility(
        source=SOURCE,
        source_id=f"sw-{pmt_id}",
        name=clean(attrs.get("FACILITY_NAME")) or clean(attrs.get("SITE_NAME")) or "Unknown",
        address=clean(attrs.get("FACILITY_ADDRESS")),
        city=clean(attrs.get("FACILITY_CITY")),
        state=clean(attrs.get("FACILITY_STATE")) or "VA",
        zip_code=clean(attrs.get("FACILITY_ZIP5")),
        county=clean(attrs.get("FACILITY_COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Solid Waste",
        last_updated=datetime.now(timezone.utc),
    )


def map_petroleum_tank(attrs: dict) -> Facility:
    """Convert Petroleum Tank Facilities attributes to a Facility model.

    Key fields: FAC_ID, FAC_NAME, FAC_ADDR1, FAC_ADDR2, FAC_CITY,
    FAC_STATE, FAC_ZIP5, FAC_TYPE, Lat, Lon, FAC_ACTIVE_UST,
    FAC_ACTIVE_AST, FAC_RISK_RANK.
    """
    fac_id = clean(attrs.get("FAC_ID")) or ""

    addr1 = clean(attrs.get("FAC_ADDR1"))
    addr2 = clean(attrs.get("FAC_ADDR2"))
    address = ", ".join(filter(None, [addr1, addr2])) if (addr1 or addr2) else None

    fac_type = clean(attrs.get("FAC_TYPE"))
    risk_rank = clean(attrs.get("FAC_RISK_RANK"))
    programs = []
    if fac_type:
        programs.append(fac_type)
    if risk_rank:
        programs.append(f"Risk: {risk_rank}")

    return Facility(
        source=SOURCE,
        source_id=f"tank-{fac_id}",
        name=clean(attrs.get("FAC_NAME")) or clean(attrs.get("NAME")) or "Unknown",
        address=address,
        city=clean(attrs.get("FAC_CITY")),
        state=clean(attrs.get("FAC_STATE")) or "VA",
        zip_code=clean(attrs.get("FAC_ZIP5")),
        county=None,
        lat=parse_float(attrs.get("Lat")),
        lon=parse_float(attrs.get("Lon")),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Petroleum Tanks",
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Petroleum Releases (Layer 104) — confirmed petroleum release sites
# ---------------------------------------------------------------------------

def map_release_facility(feature: dict) -> Facility:
    """Create a Facility record from a petroleum release site.

    Each release gets its own facility entry (source_id=rst-{RST_ID})
    with the address and coordinates from the release record.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)
    # Fall back to explicit Lat/Lon fields if geometry absent
    if lat is None:
        lat = parse_float(attrs.get("Lat"))
    if lon is None:
        lon = parse_float(attrs.get("Lon"))

    rst_id = attrs.get("RST_ID")
    rst_id_str = str(int(rst_id)) if rst_id is not None else ""

    status = clean(attrs.get("RST_STATUS_IND")) or ""
    priority = clean(attrs.get("RST_PRIORITY"))
    programs = ["Petroleum Release"]
    if status:
        programs.append(status)
    if priority:
        programs.append(f"Priority {priority}")

    return Facility(
        source=SOURCE,
        source_id=f"rst-{rst_id_str}",
        name=clean(attrs.get("RST_NAME")) or "Unknown",
        address=clean(attrs.get("FAC_L_ADDR_1")),
        city=clean(attrs.get("FAC_L_CITY")),
        state=clean(attrs.get("FAC_L_STATE")) or "VA",
        zip_code=clean(attrs.get("FAC_L_ZIP5")),
        county=clean(attrs.get("FIC_DESCRIPTION")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )


def map_release_violation(feature: dict) -> Violation:
    """Convert a petroleum release record to a Violation.

    All petroleum releases are confirmed violations (leaks/spills).
    Status is Open (ongoing remediation) or Closed (cleanup complete).
    """
    attrs = feature.get("attributes", {})

    rst_id = attrs.get("RST_ID")
    rst_id_str = str(int(rst_id)) if rst_id is not None else ""
    complaint_no = clean(attrs.get("RST_POLL_COMPLAINT_NO")) or ""

    status = clean(attrs.get("RST_STATUS_IND")) or ""
    priority = clean(attrs.get("RST_PRIORITY"))
    name = clean(attrs.get("RST_NAME")) or ""
    county = clean(attrs.get("FIC_DESCRIPTION")) or ""

    # Build description
    desc_parts = []
    if name:
        desc_parts.append(name)
    if county:
        desc_parts.append(county)
    if complaint_no:
        desc_parts.append(f"Complaint #{complaint_no}")

    severity = "Open" if status == "Open" else "Resolved"
    if priority:
        p = str(priority)
        if p == "1":
            severity = "Significant" if status == "Open" else "Resolved"

    return Violation(
        source=SOURCE,
        source_id=f"rst-{rst_id_str}",
        facility_source_id=f"rst-{rst_id_str}",
        facility_source=SOURCE,
        violation_type="Petroleum Release",
        violation_date=epoch_ms_to_date(attrs.get("RST_RELEASE_REPORTED")),
        statute="VA Petroleum Storage Tank Program",
        program_area="Petroleum",
        severity=severity,
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# PReP Reports (Layer 175) — pollution response incidents
# ---------------------------------------------------------------------------

_PRP_NON_VIOLATION_REASONS = {
    "no pollution observed",
}


def has_prep_violation(feature: dict) -> bool:
    """Filter PReP reports that aren't real violations.

    Exclude closed reports where investigation found no pollution.
    """
    attrs = feature.get("attributes", {})
    status = (clean(attrs.get("RSC_STATUS_DESCRIPTION")) or "").lower()
    reason = (clean(attrs.get("SRC_STATUS_REASON_DESC")) or "").lower()

    if status == "closed" and reason in _PRP_NON_VIOLATION_REASONS:
        return False
    return True


def map_prep_facility(feature: dict) -> Facility:
    """Create a Facility record from a PReP pollution report.

    Each report gets its own facility entry (source_id=prp-{REPORT_ID})
    with the address and coordinates from the report.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    report_id = attrs.get("PRP_REPORT_ID")
    report_id_str = str(int(report_id)) if report_id is not None else ""

    status = clean(attrs.get("RSC_STATUS_DESCRIPTION")) or ""
    programs = ["Pollution Response"]
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"prp-{report_id_str}",
        name=clean(attrs.get("PRP_SITE_NAME")) or "Unknown",
        address=clean(attrs.get("PRP_SITE_ADDRESS1")),
        city=clean(attrs.get("PRP_SITE_CITY")),
        state=clean(attrs.get("PRP_SITE_STATE")) or "VA",
        zip_code=clean(attrs.get("PRP_SITE_ZIP_CODE")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs),
        last_updated=datetime.now(timezone.utc),
    )


def map_prep_violation(feature: dict) -> Violation:
    """Convert a PReP pollution report to a Violation.

    PReP reports track pollution incidents (oil spills, contamination events, etc.).
    Status: New, Under Investigation, Closed.
    """
    attrs = feature.get("attributes", {})

    report_id = attrs.get("PRP_REPORT_ID")
    report_id_str = str(int(report_id)) if report_id is not None else ""

    status = clean(attrs.get("RSC_STATUS_DESCRIPTION")) or ""
    reason = clean(attrs.get("SRC_STATUS_REASON_DESC"))
    name = clean(attrs.get("PRP_SITE_NAME")) or ""

    desc_parts = []
    if name:
        desc_parts.append(name)
    if reason:
        desc_parts.append(reason)

    if status.lower() in ("new", "under investigation"):
        severity = "Open"
    else:
        severity = "Resolved"

    return Violation(
        source=SOURCE,
        source_id=f"prp-{report_id_str}",
        facility_source_id=f"prp-{report_id_str}",
        facility_source=SOURCE,
        violation_type="Pollution Incident",
        violation_date=epoch_ms_to_date(attrs.get("PRP_INCIDENT_DATE_TIME")),
        statute="VA Pollution Response Program",
        program_area="Pollution Response",
        severity=severity,
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
