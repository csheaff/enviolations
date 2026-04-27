"""Map raw Alabama ADEM ArcGIS feature data to Pydantic models.

AL ADEM data comes from ArcGIS at gis.adem.alabama.gov.
Facility datasets:
  - UST_SWAA_FieldOps (Active UST Sites): SITE_NAME, SITE_ADDRESS, SITE_CITY,
    SITE_ZIP, GPS_LAT_DEC_DEG, GPS_LONG_DEC_DEG
  - Landfills2024: FACILITY_NAME, ADDRESS, CITY, COUNTY_NAME,
    LAT_DECIMAL, LON_DECIMAL, PERMIT_NO
  - Brownfields (FeatureServer): SiteName, Address, City, County, Lat, Lon
Violation datasets:
  - SSO Reports (SSO_all_Dates_project/0) → facilities + violations (8.6K)
  - UST Incidents (UST_Incidents_GCS/0) → facilities + violations (5.4K)

Coordinates come from explicit lat/lon attribute fields (not geometry).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import parse_float, clean, epoch_ms_to_date

SOURCE = "al_adem"

def map_ust_site(feature: dict) -> Facility:
    """Convert a UST_SWAA_FieldOps Active UST Site feature to a Facility."""
    attrs = feature.get("attributes", {})

    county_code = clean(attrs.get("SITE_ID_COUNTY")) or ""
    site_num = clean(attrs.get("SITE_ID_NUMBER")) or ""
    source_id = f"ust-{county_code}-{site_num}" if county_code and site_num else f"ust-{attrs.get('SITE_SEQ_NUMBER', '')}"

    lat = parse_float(attrs.get("GPS_LAT_DEC_DEG"), zero_as_none=True)
    lon = parse_float(attrs.get("GPS_LONG_DEC_DEG"), zero_as_none=True)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SITE_NAME")) or "Unknown",
        address=clean(attrs.get("SITE_ADDRESS")),
        city=clean(attrs.get("SITE_CITY")),
        state="AL",
        zip_code=clean(attrs.get("SITE_ZIP")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="UST",
        last_updated=datetime.now(timezone.utc),
    )

def map_landfill(feature: dict) -> Facility:
    """Convert a Landfills2024 feature to a Facility."""
    attrs = feature.get("attributes", {})

    permit_no = clean(attrs.get("PERMIT_NO")) or ""
    fac_id = clean(attrs.get("FACILITY_ID")) or ""
    source_id = f"landfill-{permit_no}" if permit_no else f"landfill-{fac_id}"

    lat = parse_float(attrs.get("LAT_DECIMAL"), zero_as_none=True)
    lon = parse_float(attrs.get("LON_DECIMAL"), zero_as_none=True)

    programs = []
    fa_type = clean(attrs.get("FA_TYPE"))
    if fa_type:
        programs.append(fa_type)
    status = clean(attrs.get("STATUS"))
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FACILITY_NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="AL",
        zip_code=clean(attrs.get("ZIPCODE")),
        county=clean(attrs.get("COUNTY_NAME")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Landfill",
        last_updated=datetime.now(timezone.utc),
    )

def map_brownfield(feature: dict) -> Facility:
    """Convert a Brownfields FeatureServer feature to a Facility."""
    attrs = feature.get("attributes", {})

    site_num = clean(attrs.get("SiteNum")) or ""
    fcode = clean(attrs.get("Fcode")) or ""
    source_id = f"brownfield-{site_num}" if site_num else f"brownfield-{fcode}"

    lat = parse_float(attrs.get("Lat"), zero_as_none=True)
    lon = parse_float(attrs.get("Lon"), zero_as_none=True)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("SiteName")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("City")),
        state="AL",
        zip_code=None,
        county=clean(attrs.get("County")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs="Brownfield",
        last_updated=datetime.now(timezone.utc),
    )

# ---------------------------------------------------------------------------
# SSO Reports (SSO_all_Dates_project/MapServer/0)
# ---------------------------------------------------------------------------

def _parse_sso_date(val) -> date | None:
    """Parse SSO date string (e.g. '2017-06-21')."""
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.split("T")[0]).date()
    except (ValueError, TypeError):
        return None

def _parse_volume_gallons(val) -> float | None:
    """Parse SSO estimated volume string to gallons."""
    if not val:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None

def _sso_severity(attrs: dict) -> str:
    """Derive severity from SSO volume and duration.

    >= 100,000 gallons → High (major overflow)
    >= 1,000 gallons → Medium
    Otherwise → Low
    """
    vol = _parse_volume_gallons(attrs.get("est_volume"))
    if vol is not None:
        if vol >= 100_000:
            return "High"
        if vol >= 1_000:
            return "Medium"
    return "Low"

def map_sso_facility(attrs: dict) -> Facility:
    """Convert an SSO Reports feature to a Facility.

    Key fields: facility_id, facility_site, permit_no, permittee,
    LATTITUDE_DECIMAL, LONGITUDE_DECIMAL.
    """
    permit_no = clean(attrs.get("permit_no")) or ""
    facility_id = clean(attrs.get("facility_id")) or ""
    source_id = f"sso-{permit_no}" if permit_no else f"sso-fac-{facility_id}"

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=(clean(attrs.get("facility_site"))
              or clean(attrs.get("permittee"))
              or "Unknown"),
        address=clean(attrs.get("location_of_discharge")),
        city=None,
        state="AL",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("LATTITUDE_DECIMAL"), zero_as_none=True),
        lon=parse_float(attrs.get("LONGITUDE_DECIMAL"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs="SSO",
        last_updated=datetime.now(timezone.utc),
    )

def map_sso_violation(attrs: dict) -> Violation:
    """Convert an SSO Reports feature to a Violation.

    Key fields: sso_report_id, permit_no, facility_site,
    date_sso_began, est_volume, cause_of_discharge, receiving_stream.
    """
    report_id = clean(attrs.get("sso_report_id")) or ""
    permit_no = clean(attrs.get("permit_no")) or ""
    facility_id = clean(attrs.get("facility_id")) or ""
    fac_source_id = f"sso-{permit_no}" if permit_no else f"sso-fac-{facility_id}"

    desc_parts = []
    site = clean(attrs.get("facility_site"))
    if site:
        desc_parts.append(site)
    volume = clean(attrs.get("est_volume"))
    if volume:
        desc_parts.append(f"Volume: {volume} gallons")
    cause = clean(attrs.get("cause_of_discharge"))
    if cause:
        desc_parts.append(f"Cause: {cause}")
    stream = clean(attrs.get("receiving_stream"))
    if stream:
        desc_parts.append(f"Receiving stream: {stream}")

    return Violation(
        source=SOURCE,
        source_id=f"sso-{report_id}",
        facility_source_id=fac_source_id,
        facility_source=SOURCE,
        violation_type="SSO Overflow",
        violation_date=_parse_sso_date(attrs.get("date_sso_began")),
        statute=None,
        program_area="Water",
        severity=_sso_severity(attrs),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )

# ---------------------------------------------------------------------------
# UST Incidents (UST_Incidents_GCS/MapServer/0)
# ---------------------------------------------------------------------------

def map_ust_incident_facility(attrs: dict) -> Facility:
    """Convert a UST Incidents feature to a Facility.

    Key fields: FACILITY_NUMBER, SITE_NAME, SITE_ADDRESS, SITE_CITY,
    OWNER_NAME, GPS_LAT_DEC_DEG, GPS_LONG_DEC_DEG.
    """
    fac_num = clean(attrs.get("FACILITY_NUMBER")) or ""
    incident_num = clean(attrs.get("SEARCHABLE_INCIDENT_NUMBER")) or ""
    source_id = f"ust-inc-{fac_num}" if fac_num else f"ust-inc-{incident_num}"

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=(clean(attrs.get("SITE_NAME"))
              or clean(attrs.get("OWNER_NAME"))
              or "Unknown"),
        address=clean(attrs.get("SITE_ADDRESS")),
        city=clean(attrs.get("SITE_CITY")),
        state="AL",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("GPS_LAT_DEC_DEG"), zero_as_none=True),
        lon=parse_float(attrs.get("GPS_LONG_DEC_DEG"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs="UST Incident",
        last_updated=datetime.now(timezone.utc),
    )

def map_ust_incident_violation(attrs: dict) -> Violation:
    """Convert a UST Incidents feature to a Violation.

    Key fields: SEARCHABLE_INCIDENT_NUMBER, FACILITY_NUMBER, SITE_NAME,
    DATE_REPORTED, DATE_END_CLEANUP, OWNER_NAME.
    """
    incident_num = clean(attrs.get("SEARCHABLE_INCIDENT_NUMBER")) or ""
    fac_num = clean(attrs.get("FACILITY_NUMBER")) or ""
    fac_source_id = f"ust-inc-{fac_num}" if fac_num else f"ust-inc-{incident_num}"

    desc_parts = []
    site = clean(attrs.get("SITE_NAME"))
    if site:
        desc_parts.append(site)
    owner = clean(attrs.get("OWNER_NAME"))
    if owner:
        desc_parts.append(f"Owner: {owner}")
    address = clean(attrs.get("SITE_ADDRESS"))
    if address:
        desc_parts.append(f"Address: {address}")

    return Violation(
        source=SOURCE,
        source_id=f"ust-inc-{incident_num}",
        facility_source_id=fac_source_id,
        facility_source=SOURCE,
        violation_type="UST Incident",
        violation_date=epoch_ms_to_date(attrs.get("DATE_REPORTED")),
        statute=None,
        program_area="Storage Tanks",
        severity="Medium",
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
