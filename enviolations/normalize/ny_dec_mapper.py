"""Map raw NY DEC Socrata JSON records to Pydantic models.

NY DEC data is fetched from data.ny.gov via the SODA JSON API, plus
enforcement data from the NYS GIS ArcGIS FeatureServer. Datasets:
  - Environmental Remediation Sites → Facility (Socrata)
  - Solid Waste Management Facilities → Facility (Socrata)
  - Spill Incidents → Violation (Socrata, not ingested — no facility linkage)
  - Orders on Consent → Violation + Facility (ArcGIS, 1.1K consent orders)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, extract_arcgis_coords, epoch_ms_to_date, parse_date

SOURCE = "ny_dec"


def _parse_date(val: str | None) -> date | None:
    return parse_date(val, formats=("%Y-%m-%d", "%m/%d/%Y"))


def _clean_zip(val) -> str | None:
    """Normalize a zip code value from Socrata JSON.

    Socrata returns numeric fields as floats, so zip codes arrive as "12345.0"
    instead of "12345". Strip the decimal suffix before passing to the Facility
    validator. Also handles leading zeros (zfill to 5) for 4-digit values.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # Strip trailing ".0" from float-as-string (e.g. "12345.0" → "12345")
    if s.endswith(".0"):
        s = s[:-2]
    # Strip trailing ".00", ".000" etc. for over-precise floats
    elif "." in s:
        try:
            s = str(int(float(s)))
        except (ValueError, TypeError):
            pass
    return s or None


def map_remediation_site(row: dict) -> Facility:
    """Convert an Environmental Remediation Sites JSON record to a Facility.

    SODA fields: program_number, program_facility_name, address1, locality,
    zipcode, county, latitude, longitude, program_type, site_class, dec_region.
    """
    program_num = clean(row.get("program_number")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"rem-{program_num}",
        name=clean(row.get("program_facility_name")) or "Unknown",
        address=clean(row.get("address1")),
        city=clean(row.get("locality")),
        state="NY",
        zip_code=_clean_zip(row.get("zipcode")),
        county=clean(row.get("county")),
        lat=parse_float(row.get("latitude")),
        lon=parse_float(row.get("longitude")),
        naics_codes=None,
        sic_codes=None,
        programs=clean(row.get("program_type")),
        last_updated=datetime.now(timezone.utc),
    )


def map_solid_waste_facility(row: dict) -> Facility:
    """Convert a Solid Waste Management Facilities JSON record to a Facility.

    SODA fields: authorization_number, facility_name, location_address,
    city, state, zip_code, county, activity_desc, waste_types, georeference.
    """
    auth_num = clean(row.get("authorization_number")) or ""

    # georeference may contain lat/lon as a nested object or point string
    lat = None
    lon = None
    georef = row.get("georeference")
    if isinstance(georef, dict):
        lat = parse_float(georef.get("latitude"))
        lon = parse_float(georef.get("longitude"))

    return Facility(
        source=SOURCE,
        source_id=f"sw-{auth_num}",
        name=clean(row.get("facility_name")) or "Unknown",
        address=clean(row.get("location_address")),
        city=clean(row.get("city")),
        state=clean(row.get("state")) or "NY",
        zip_code=_clean_zip(row.get("zip_code")),
        county=clean(row.get("county")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=clean(row.get("activity_desc")),
        last_updated=datetime.now(timezone.utc),
    )


def _spill_severity(quantity, material_family: str | None) -> str | None:
    """Derive severity from spill quantity and material type."""
    qty = parse_float(quantity)
    fam = (material_family or "").lower()
    if fam in ("hazardous material", "hazardous waste"):
        return "High"
    if qty is not None and qty > 1000:
        return "High"
    if qty is not None and qty > 100:
        return "Medium"
    if qty is not None and qty > 0:
        return "Low"
    return None


def _spill_description(row: dict) -> str | None:
    """Build a description string from spill fields."""
    parts = []
    material = clean(row.get("material_name"))
    if material:
        parts.append(material)
    factor = clean(row.get("contributing_factor"))
    if factor:
        parts.append(f"Cause: {factor}")
    waterbody = clean(row.get("waterbody"))
    if waterbody:
        parts.append(f"Waterbody: {waterbody}")
    return "; ".join(parts) if parts else None


def map_spill(row: dict) -> Violation:
    """Convert a Spill Incidents JSON record to a Violation.

    SODA fields: spill_number, spill_date, close_date, material_name,
    material_family, contributing_factor, quantity, units, county,
    locality, waterbody, source.
    """
    spill_num = clean(row.get("spill_number")) or ""

    return Violation(
        source=SOURCE,
        source_id=f"spill-{spill_num}",
        facility_source_id=f"spill-{spill_num}",
        facility_source=SOURCE,
        violation_type="SPILL",
        violation_date=_parse_date(row.get("spill_date")),
        statute=None,
        program_area=clean(row.get("material_family")),
        severity=_spill_severity(row.get("quantity"), row.get("material_family")),
        description=_spill_description(row),
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Orders on Consent (ArcGIS FeatureServer) — DEC enforcement orders
# ---------------------------------------------------------------------------

def map_consent_order_facility(feature: dict) -> Facility:
    """Create a Facility record from a consent order.

    Each consent order gets its own facility entry with respondent name,
    city, zip, and coordinates from the ArcGIS feature.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    case_num = clean(attrs.get("DEC_CASE_NUM")) or ""
    respondent = clean(attrs.get("RESPONDENT")) or "Unknown"
    city = clean(attrs.get("RESPONDENT_CITY"))
    zip_code = _clean_zip(attrs.get("RESPONDENT_ZIP"))

    return Facility(
        source=SOURCE,
        source_id=f"oc-{case_num}",
        name=respondent,
        address=None,
        city=city,
        state="NY",
        zip_code=zip_code,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Consent Order",
        last_updated=datetime.now(timezone.utc),
    )


def map_consent_order_violation(feature: dict) -> Violation:
    """Convert a consent order to a Violation.

    Consent orders are legally binding enforcement agreements
    following environmental law violations.
    """
    attrs = feature.get("attributes", {})

    case_num = clean(attrs.get("DEC_CASE_NUM")) or ""
    respondent = clean(attrs.get("RESPONDENT")) or ""

    desc_parts = []
    if respondent:
        desc_parts.append(respondent)
    desc_parts.append(f"Case {case_num}")

    return Violation(
        source=SOURCE,
        source_id=f"oc-{case_num}",
        facility_source_id=f"oc-{case_num}",
        facility_source=SOURCE,
        violation_type="Consent Order",
        violation_date=epoch_ms_to_date(attrs.get("DATE_ADDRESSED")),
        statute="NY Environmental Conservation Law",
        program_area="Enforcement",
        severity="Significant",
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
