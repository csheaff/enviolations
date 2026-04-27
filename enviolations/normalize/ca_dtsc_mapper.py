"""Map raw CA DTSC ArcGIS feature attributes to Pydantic models.

CA DTSC data comes from ArcGIS REST API endpoints. This mapper handles
datasets from the EnviroStor system:
  - Cleanup Sites → Facility + Violation (active sites)
  - Hazardous Waste Sites (Permitted facilities) → Facility

Active cleanup sites (status="Active", "Inactive - Action Required", etc.)
are treated as violations, similar to how CA GeoTracker open LUST cases
are treated. The contamination/enforcement action is the violation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, parse_date, normalize_ca_city

SOURCE = "ca_dtsc"

# Statuses indicating an active enforcement/cleanup action (= violation)
_ACTIVE_STATUSES = {
    "Active",
    "Inactive - Action Required",
    "Inactive - Needs Evaluation",
}

# Site types that map to high-risk program areas
_SITE_TYPE_TO_PROGRAM: dict[str, str] = {
    "Federal Superfund": "Federal Superfund",
    "State Response": "State Response",
    "Voluntary Cleanup": "Voluntary Cleanup",
    "Corrective Action": "Corrective Action",
    "Tiered Permit": "Tiered Permit",
    "School Cleanup": "School Cleanup",
    "Evaluation": "Site Evaluation",
    "Historical": "Historical",
    "SMBRP": "SMBRP",
}


def _parse_date(val: str | None) -> date | None:
    # CA DTSC ArcGIS API returns dates as 'M/D/YYYY HH:MM:SS AM/PM'
    # parse_date() default format only handles ISO '%Y-%m-%d', so we add the
    # ArcGIS-style format explicitly. ISO fallback handles future sources.
    return parse_date(val, formats=("%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d"))


def _get_severity(status: str | None, site_type: str | None) -> str | None:
    """Derive severity from site status and type."""
    if not status:
        return None
    s = status.lower()
    st = (site_type or "").lower()
    if "superfund" in st or "state response" in st:
        return "High"
    if "action required" in s:
        return "High"
    if "active" in s and ("corrective" in st or "voluntary cleanup" in st):
        return "Medium"
    if "active" in s:
        return "Low"
    return None


def map_cleanup_site(attrs: dict) -> Facility:
    """Convert an EnviroStor Cleanup Site feature to a Facility model.

    Key fields: envirostor_id, project_name, address, city, zip, county,
    latitude, longitude, site_type, status, lead_agency.
    """
    site_code = (clean(attrs.get("envirostor_id"))
                 or clean(attrs.get("ENVIROSTOR_ID"))
                 or clean(attrs.get("SITE_CODE"))
                 or "")

    program_parts = []
    site_type = clean(attrs.get("site_type"))
    if site_type:
        mapped = _SITE_TYPE_TO_PROGRAM.get(site_type, site_type)
        if mapped and mapped.upper() != "NONE SPECIFIED":
            program_parts.append(mapped)

    return Facility(
        source=SOURCE,
        source_id=f"cleanup-{site_code}",
        name=clean(attrs.get("project_name")) or clean(attrs.get("SITE_NAME")) or "Unknown",
        address=clean(attrs.get("address")) or clean(attrs.get("ADDRESS")),
        city=normalize_ca_city(clean(attrs.get("city")) or clean(attrs.get("CITY"))),
        state="CA",
        zip_code=clean(attrs.get("zip")) or clean(attrs.get("ZIP")),
        county=clean(attrs.get("county")) or clean(attrs.get("COUNTY")),
        lat=parse_float(attrs.get("latitude") or attrs.get("LATITUDE")),
        lon=parse_float(attrs.get("longitude") or attrs.get("LONGITUDE")),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(program_parts) if program_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


def map_hazwaste_site(attrs: dict) -> Facility:
    """Convert an EnviroStor Hazardous Waste Site feature to a Facility model.

    Key fields: epa_id, site_code, facility_name, address, city, zip, county,
    latitude, longitude, permit_type, facility_status.
    """
    epa_id = clean(attrs.get("epa_id")) or clean(attrs.get("EPA_ID")) or ""
    site_code = clean(attrs.get("site_code")) or clean(attrs.get("SITE_CODE")) or ""
    source_id = f"hazwaste-{epa_id}" if epa_id else f"hazwaste-{site_code}"

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("facility_name")) or clean(attrs.get("SITE_NAME")) or "Unknown",
        address=clean(attrs.get("address")) or clean(attrs.get("ADDRESS")),
        city=normalize_ca_city(clean(attrs.get("city")) or clean(attrs.get("CITY"))),
        state="CA",
        zip_code=clean(attrs.get("zip")) or clean(attrs.get("ZIP")),
        county=clean(attrs.get("county")) or clean(attrs.get("COUNTY")),
        lat=parse_float(attrs.get("latitude") or attrs.get("LATITUDE")),
        lon=parse_float(attrs.get("longitude") or attrs.get("LONGITUDE")),
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("permit_type")) or clean(attrs.get("PROGRAM")),
        last_updated=datetime.now(timezone.utc),
    )


def is_violation_record(attrs: dict) -> bool:
    """Return True if this cleanup site should generate a Violation.

    Only active enforcement/cleanup sites generate violations. Completed,
    certified, or purely informational sites do not.
    """
    status = clean(attrs.get("status") or attrs.get("STATUS"))
    return status in _ACTIVE_STATUSES


def map_cleanup_violation(attrs: dict) -> Violation:
    """Convert an active DTSC cleanup site to a Violation.

    Each active cleanup site represents an acknowledged contamination
    event under DTSC oversight. The envirostor_id links the violation
    back to its cleanup-{envirostor_id} facility record.

    Key fields: envirostor_id, site_type, status, status_date,
    potential_coc, confirmed_coc, national_priorities_list.
    """
    site_code = (clean(attrs.get("envirostor_id"))
                 or clean(attrs.get("ENVIROSTOR_ID"))
                 or clean(attrs.get("SITE_CODE"))
                 or "")

    site_type = clean(attrs.get("site_type") or attrs.get("SITE_TYPE"))
    status = clean(attrs.get("status") or attrs.get("STATUS"))
    status_date = _parse_date(attrs.get("status_date") or attrs.get("STATUS_DATE"))

    # Build description
    desc_parts = []
    if site_type:
        desc_parts.append(f"Site Type: {site_type}")
    if status:
        desc_parts.append(f"Status: {status}")
    confirmed_coc = clean(attrs.get("confirmed_coc") or attrs.get("CONFIRMED_COC"))
    potential_coc = clean(attrs.get("potential_coc") or attrs.get("POTENTIAL_COC"))
    if confirmed_coc:
        desc_parts.append(f"Confirmed Contaminants: {confirmed_coc}")
    elif potential_coc:
        desc_parts.append(f"Potential Contaminants: {potential_coc}")
    npl = clean(attrs.get("national_priorities_list") or attrs.get("NATIONAL_PRIORITIES_LIST"))
    if npl and npl.upper() == "YES":
        desc_parts.append("National Priorities List (NPL/Superfund)")

    program_area = _SITE_TYPE_TO_PROGRAM.get(site_type or "", "Cleanup")

    return Violation(
        source=SOURCE,
        source_id=f"cleanup-vio-{site_code}",
        facility_source_id=f"cleanup-{site_code}",
        facility_source=SOURCE,
        violation_type=site_type or "Cleanup Site",
        violation_date=status_date,
        statute=None,
        program_area=program_area,
        severity=_get_severity(status, site_type),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
