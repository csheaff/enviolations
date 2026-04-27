"""Map raw GA EPD feature attributes to Pydantic models.

GA EPD data comes from two sources:
  - ArcGIS Online FeatureServer: Hazardous Site Inventory → Facility (509 sites)
  - ENFO REST API (enfo.gaepd.org): Enforcement Orders → facilities + violations (22K+)

The ENFO API returns paginated JSON with enforcement orders. Each order has a
facilityName and county but no coordinates. Facilities are deduplicated by
normalized (name, county) pair.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float

SOURCE = "ga_epd"


def map_hsi_site(attrs: dict) -> Facility:
    """Convert a Hazardous Site Inventory feature to a Facility model.

    Key fields: HSI__, Site_Name, Address, City, County, Latitude,
    Longitude, Class, Site_Summary, Investigation___Cleanup_Funding.
    """
    hsi_num = clean(attrs.get("HSI__")) or ""

    programs_parts = []
    site_class = clean(attrs.get("Class"))
    if site_class:
        programs_parts.append(f"Class: {site_class}")
    funding = clean(attrs.get("Investigation___Cleanup_Funding"))
    if funding:
        programs_parts.append(funding)

    return Facility(
        source=SOURCE,
        source_id=f"hsi-{hsi_num}",
        name=clean(attrs.get("Site_Name")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("City")),
        state="GA",
        zip_code=None,
        county=clean(attrs.get("County")),
        lat=parse_float(attrs.get("Latitude")),
        lon=parse_float(attrs.get("Longitude")),
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts) if programs_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# ENFO Enforcement Orders (enfo.gaepd.org/api/EnforcementOrder)
# ---------------------------------------------------------------------------

_AUTHORITY_PROGRAM = {
    "Air Quality Act": "Air",
    "Asbestos Safety Act": "Air",
    "Motor Vehicle Inspection and Maintenance Act": "Air",
    "Comprehensive Solid Waste Management Act": "Solid Waste",
    "Erosion and Sedimentation Act": "Water",
    "Hazardous Site Response Act": "Hazardous Waste",
    "Hazardous Waste Management Act": "Hazardous Waste",
    "Lead Poisoning Prevention Act": "Hazardous Waste",
    "Oil or Hazardous Materials Spills or Releases Act": "Hazardous Waste",
    "Radiation Control Act": "Hazardous Waste",
    "Safe Drinking Water Act": "SDWA",
    "Underground Storage Tank Act": "UST",
    "Water Quality Control Act": "Water",
    "Water Well Standards Act": "Water",
    "Groundwater Use Act": "Water",
    "River Basin Management Planning Act": "Water",
    "Oil and Gas and Deep Drilling Act": "Mining",
    "Surface Mining Act": "Mining",
    "Safe Dams Act": "Water",
    "Voluntary Remediation Program Act": "Remediation",
    "Georgia Environmental Policy Act": "Environmental Policy",
}


def _normalize_facility_key(name: str, county: str) -> str:
    """Create a stable key from facility name + county for dedup."""
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    c = re.sub(r"[^a-z0-9]", "", county.lower()) if county else ""
    return f"{n}-{c}"


def _parse_enfo_date(val) -> date | None:
    """Parse ENFO date string (e.g. '2026-01-21T00:00:00')."""
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.split("T")[0]).date()
    except (ValueError, TypeError):
        return None


def _enfo_severity(item: dict) -> str:
    """Derive severity from enforcement order settlement amount.

    >= $10,000 → High (major enforcement)
    >= $1,000 → Medium
    Otherwise → Low
    """
    amount = item.get("settlementAmount") or 0
    if amount >= 10_000:
        return "High"
    if amount >= 1_000:
        return "Medium"
    return "Low"


def map_enforcement_facility(item: dict) -> Facility:
    """Convert an ENFO enforcement order to a Facility.

    Creates one facility per unique (facilityName, county) pair.
    Key fields: facilityName, county, legalAuthority.
    """
    name = clean(item.get("facilityName")) or "Unknown"
    county = clean(item.get("county")) or ""
    key = _normalize_facility_key(name, county)

    authority = item.get("legalAuthority") or {}
    auth_name = clean(authority.get("authorityName")) or ""
    program = _AUTHORITY_PROGRAM.get(auth_name, auth_name)

    return Facility(
        source=SOURCE,
        source_id=f"enfo-{key}",
        name=name,
        address=None,
        city=None,
        state="GA",
        zip_code=None,
        county=county if county else None,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs=program or None,
        last_updated=datetime.now(timezone.utc),
    )


def map_enforcement_violation(item: dict) -> Violation:
    """Convert an ENFO enforcement order to a Violation.

    Key fields: id, orderNumber, facilityName, county, legalAuthority,
    executedDate, cause, requirements, settlementAmount.
    """
    order_id = item.get("id", "")
    name = clean(item.get("facilityName")) or "Unknown"
    county = clean(item.get("county")) or ""
    fac_key = _normalize_facility_key(name, county)

    authority = item.get("legalAuthority") or {}
    auth_name = clean(authority.get("authorityName")) or ""
    program = _AUTHORITY_PROGRAM.get(auth_name, auth_name)

    desc_parts = []
    if name != "Unknown":
        desc_parts.append(name)
    order_num = clean(item.get("orderNumber"))
    if order_num:
        desc_parts.append(f"Order: {order_num}")
    cause = clean(item.get("cause"))
    if cause:
        desc_parts.append(cause)
    requirements = clean(item.get("requirements"))
    if requirements:
        desc_parts.append(f"Requirements: {requirements}")
    amount = item.get("settlementAmount")
    if amount and amount > 0:
        desc_parts.append(f"Settlement: ${amount:,.0f}")

    vio_date = _parse_enfo_date(item.get("executedDate"))
    if not vio_date:
        vio_date = _parse_enfo_date(item.get("proposedOrderPostedDate"))

    return Violation(
        source=SOURCE,
        source_id=f"enfo-{order_id}",
        facility_source_id=f"enfo-{fac_key}",
        facility_source=SOURCE,
        violation_type="Enforcement Order",
        violation_date=vio_date,
        statute=auth_name or None,
        program_area=program or None,
        severity=_enfo_severity(item),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
