"""Map raw IL EPA feature attributes to Pydantic models.

IL EPA data comes from ArcGIS REST services at geoservices.epa.illinois.gov
and Socrata open data at data.illinois.gov.
Facility datasets:
  - Federal Facilities Unit Sites → Facility
  - Illinois Landfills (Active + Post-Closure layers) → Facility
Violation datasets:
  - LUST Incidents (Socrata eucw-j9dg) → Facility + Violation (26K)
  - OER Incidents (ArcGIS EouIncidentTracker) → Facility + Violation (2.4K)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, epoch_ms_to_date

SOURCE = "il_epa"


def map_federal_facility(attrs: dict) -> Facility:
    """Convert a Federal Facilities Unit Sites feature to a Facility model.

    Key fields: IEPANUMBER, Site_Name, ADDRESS, CITY, COUNTY, STATE,
    ZIP_CODE, LATITUDE, LONGITUDE, Site_Type.
    """
    iepa_num = clean(attrs.get("IEPANUMBER")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"fed-{iepa_num}",
        name=clean(attrs.get("Site_Name")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state=clean(attrs.get("STATE")) or "IL",
        zip_code=clean(attrs.get("ZIP_CODE")),
        county=clean(attrs.get("COUNTY")),
        lat=parse_float(attrs.get("LATITUDE")),
        lon=parse_float(attrs.get("LONGITUDE")),
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("Site_Type")),
        last_updated=datetime.now(timezone.utc),
    )


def map_landfill(attrs: dict, landfill_type: str = "Active") -> Facility:
    """Convert an Illinois Landfills feature to a Facility model.

    Key fields: SiteID, SiteName, StreetAddress, City, ZipCode, County,
    Latitude, Longitude.
    """
    site_id = clean(attrs.get("SiteID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"lf-{site_id}",
        name=clean(attrs.get("SiteName")) or "Unknown",
        address=clean(attrs.get("StreetAddress")),
        city=clean(attrs.get("City")),
        state="IL",
        zip_code=clean(attrs.get("ZipCode")),
        county=clean(attrs.get("County")),
        lat=parse_float(attrs.get("Latitude")),
        lon=parse_float(attrs.get("Longitude")),
        naics_codes=None,
        sic_codes=None,
        programs=f"Landfill ({landfill_type})",
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# LUST Incidents (Socrata: data.illinois.gov eucw-j9dg)
# ---------------------------------------------------------------------------

def _parse_socrata_date(val) -> date | None:
    """Parse a Socrata ISO-ish date string (e.g. '2024-01-15T00:00:00.000')."""
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def _lust_substances(rec: dict) -> str | None:
    """Build a comma-separated list of released substances from boolean flags."""
    substances = []
    for key, label in [
        ("gasoline", "Gasoline"), ("unleaded", "Unleaded"),
        ("diesel", "Diesel"), ("fuel_oil", "Fuel Oil"),
        ("jet_fuel", "Jet Fuel"), ("used_oil", "Used Oil"),
        ("non_petroleum_product", "Non-Petroleum"),
        ("other_petroleum", "Other Petroleum"),
    ]:
        val = rec.get(key)
        if val in (True, "true", "True", "1", 1):
            substances.append(label)
    return ", ".join(substances) if substances else None


def map_lust_facility(rec: dict) -> Facility:
    """Convert a LUST Incidents Socrata record to a Facility.

    Key fields: incident, lpc, site_name, site_city, site_county,
    site_zip, site_state, site_location.
    """
    incident = clean(rec.get("incident")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"lust-{incident}",
        name=clean(rec.get("site_name")) or "Unknown",
        address=clean(rec.get("site_location")),
        city=clean(rec.get("site_city")),
        state=clean(rec.get("site_state")) or "IL",
        zip_code=clean(rec.get("site_zip")),
        county=clean(rec.get("site_county")),
        lat=None,  # LUST dataset has no lat/lon
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs="LUST",
        last_updated=datetime.now(timezone.utc),
    )


def map_lust_violation(rec: dict) -> Violation:
    """Convert a LUST Incidents Socrata record to a Violation.

    Key fields: incident, lpc, site_name, iema_date,
    gasoline/diesel/fuel_oil/etc., primary_responsible_party.
    """
    incident = clean(rec.get("incident")) or ""

    desc_parts = []
    site_name = clean(rec.get("site_name"))
    if site_name:
        desc_parts.append(site_name)
    substances = _lust_substances(rec)
    if substances:
        desc_parts.append(f"Substances: {substances}")
    party = clean(rec.get("primary_responsible_party"))
    if party:
        desc_parts.append(f"Responsible party: {party}")

    return Violation(
        source=SOURCE,
        source_id=f"lust-{incident}",
        facility_source_id=f"lust-{incident}",
        facility_source=SOURCE,
        violation_type="LUST Incident",
        violation_date=_parse_socrata_date(rec.get("iema_date")),
        statute=None,
        program_area="Storage Tanks",
        severity="Medium",  # All LUST incidents are at least medium severity
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# OER Incidents (ArcGIS: EouIncidentTracker_DD/MapServer/2)
# ---------------------------------------------------------------------------

def _oer_severity(attrs: dict) -> str | None:
    """Derive severity from OER incident attributes."""
    material = (clean(attrs.get("MaterialReleased")) or "").lower()
    if any(h in material for h in ("hazardous", "pcb", "radioactive", "chemical")):
        return "High"
    amount = clean(attrs.get("AmountReleased"))
    if amount:
        # Try to parse numeric portion
        try:
            qty = float("".join(c for c in amount if c.isdigit() or c == "."))
            if qty > 1000:
                return "High"
            if qty > 100:
                return "Medium"
        except (ValueError, TypeError):
            pass
    if material:
        return "Medium"
    return None


def map_oer_facility(attrs: dict) -> Facility:
    """Convert an OER Incident feature to a Facility.

    Key fields: IncidentNumber, IncidentName, IncidentLocation, County,
    Latitude, Longitude, ResponsibleParty.
    """
    incident_num = clean(attrs.get("IncidentNumber")) or ""

    name = clean(attrs.get("ResponsibleParty")) or clean(attrs.get("IncidentName")) or "Unknown"

    return Facility(
        source=SOURCE,
        source_id=f"oer-{incident_num}",
        name=name,
        address=clean(attrs.get("IncidentLocation")),
        city=None,
        state="IL",
        zip_code=None,
        county=clean(attrs.get("County")),
        lat=parse_float(attrs.get("Latitude")),
        lon=parse_float(attrs.get("Longitude")),
        naics_codes=None,
        sic_codes=None,
        programs="OER Incident",
        last_updated=datetime.now(timezone.utc),
    )


def map_oer_violation(attrs: dict) -> Violation:
    """Convert an OER Incident feature to a Violation.

    Key fields: IncidentNumber, IncidentName, IncidentType,
    MaterialReleased, AmountReleased, Actions, ResponsibleParty,
    IncidentSttart.
    """
    incident_num = clean(attrs.get("IncidentNumber")) or ""

    desc_parts = []
    inc_name = clean(attrs.get("IncidentName"))
    if inc_name:
        desc_parts.append(inc_name)
    inc_type = clean(attrs.get("IncidentType"))
    if inc_type:
        desc_parts.append(f"Type: {inc_type}")
    material = clean(attrs.get("MaterialReleased"))
    if material:
        desc_parts.append(f"Material: {material}")
    amount = clean(attrs.get("AmountReleased"))
    if amount:
        desc_parts.append(f"Amount: {amount}")
    party = clean(attrs.get("ResponsibleParty"))
    if party:
        desc_parts.append(f"Responsible: {party}")

    return Violation(
        source=SOURCE,
        source_id=f"oer-{incident_num}",
        facility_source_id=f"oer-{incident_num}",
        facility_source=SOURCE,
        violation_type="OER Incident",
        violation_date=epoch_ms_to_date(attrs.get("IncidentSttart")),  # Note: typo in API field
        statute=None,
        program_area="Emergency Response",
        severity=_oer_severity(attrs),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
