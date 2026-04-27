"""Map raw MI EGLE ArcGIS feature attributes to Pydantic models.

MI EGLE data comes from ArcGIS REST services at gisagoegle.state.mi.us.
Facility datasets:
  - MmdOpenData/MapServer/0: Materials Management Facilities → Facility
  - RRDOpenData/MapServer/0: Part 201 Environmental Contamination Sites → Facility
Violation datasets:
  - Planet Detroit Air Quality Violation Notices CSV (2018-present) → Facility + Violation
    Parsed from EGLE AQD violation notice PDFs, ~1,800 records.
"""

from __future__ import annotations

import ast
import re
from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, parse_date

SOURCE = "mi_egle"


def map_mmd_facility(attrs: dict) -> Facility:
    """Convert an MMD (Materials Management Division) facility to a Facility model.

    Key fields: wdsid, legalsitename, specificsitename, facilitytype,
    addrline1, addrline2, city, state, zip, countyname,
    latdeccord, longdeccord, p115status.
    """
    wdsid = clean(attrs.get("wdsid")) or ""

    name = clean(attrs.get("legalsitename")) or clean(attrs.get("specificsitename")) or "Unknown"
    facility_type = clean(attrs.get("facilitytype"))

    return Facility(
        source=SOURCE,
        source_id=f"mmd-{wdsid}",
        name=name,
        address=clean(attrs.get("addrline1")),
        city=clean(attrs.get("city")),
        state=clean(attrs.get("state")) or "MI",
        zip_code=clean(attrs.get("zip")),
        county=clean(attrs.get("countyname")),
        lat=parse_float(attrs.get("latdeccord")),
        lon=parse_float(attrs.get("longdeccord")),
        naics_codes=None,
        sic_codes=None,
        programs=facility_type,
        last_updated=datetime.now(timezone.utc),
    )


def map_rrd_site(attrs: dict) -> Facility:
    """Convert an RRD Part 201 contamination site to a Facility model.

    Key fields: SiteID, SiteName, Address, City, ZipCode, County,
    Latitude, Longitude, RiskCondition, Contaminants, BusinessType.
    """
    site_id = clean(attrs.get("SiteID")) or ""

    programs_parts = []
    risk = clean(attrs.get("RiskCondition"))
    if risk:
        programs_parts.append(f"Risk: {risk}")
    btype = clean(attrs.get("BusinessType"))
    if btype:
        programs_parts.append(btype)

    return Facility(
        source=SOURCE,
        source_id=f"rrd-{site_id}",
        name=clean(attrs.get("SiteName")) or "Unknown",
        address=clean(attrs.get("Address")),
        city=clean(attrs.get("City")),
        state="MI",
        zip_code=clean(attrs.get("ZipCode")),
        county=clean(attrs.get("County")),
        lat=parse_float(attrs.get("Latitude")),
        lon=parse_float(attrs.get("Longitude")),
        naics_codes=None,
        sic_codes=None,
        programs="; ".join(programs_parts) if programs_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Air Quality Violation Notices (Planet Detroit CSV, 2018-present)
# ---------------------------------------------------------------------------

def _parse_comment_list(raw: str) -> list[str]:
    """Parse the comment_list field, which is a Python list serialized as string."""
    if not raw:
        return []
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return [str(c).strip() for c in parsed if c]
    except (ValueError, SyntaxError):
        pass
    return [raw.strip()] if raw.strip() else []


def _parse_date(val: str | None) -> date | None:
    return parse_date(val)


def _air_severity(rec: dict) -> str:
    """Derive severity from EPA class and violation comments."""
    epa_class = (clean(rec.get("epa_class")) or "").upper()
    if epa_class == "MAJOR":
        return "High"
    comments = (clean(rec.get("comment_list")) or "").lower()
    if any(kw in comments for kw in ("exceedance", "exceeded", "hazardous", "without a pti")):
        return "Medium"
    return "Low"


def _extract_zip(address: str | None) -> str | None:
    """Extract 5-digit zip code from address_full field."""
    if not address:
        return None
    m = re.search(r"\b(\d{5})(?:\b|-)", address)
    return m.group(1) if m else None


def map_air_violation_facility(rec: dict) -> Facility:
    """Convert a Planet Detroit Air Violation CSV record to a Facility.

    Key fields: srn, facility_name, address_full, city, county, lat, long.
    """
    srn = clean(rec.get("srn")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"air-{srn}",
        name=clean(rec.get("facility_name")) or "Unknown",
        address=clean(rec.get("address_full")),
        city=clean(rec.get("city")),
        state="MI",
        zip_code=_extract_zip(rec.get("address_full")),
        county=clean(rec.get("county")),
        lat=parse_float(rec.get("lat")),
        lon=parse_float(rec.get("long")),
        naics_codes=None,
        sic_codes=None,
        programs="Air Quality",
        last_updated=datetime.now(timezone.utc),
    )


def map_air_violation(rec: dict) -> Violation:
    """Convert a Planet Detroit Air Violation CSV record to a Violation.

    Key fields: srn, date, facility_name, comment_list, epa_class, doc_url.
    """
    srn = clean(rec.get("srn")) or ""
    vio_date = _parse_date(rec.get("date"))

    # Build description from parsed comments
    comments = _parse_comment_list(rec.get("comment_list", ""))
    desc_parts = []
    name = clean(rec.get("facility_name"))
    if name:
        desc_parts.append(name)
    for c in comments:
        desc_parts.append(c)
    doc_url = clean(rec.get("doc_url"))
    if doc_url:
        desc_parts.append(f"Source: {doc_url}")

    # Use date as part of source_id for uniqueness (one facility can have multiple VNs)
    date_str = rec.get("date", "").replace("-", "") if rec.get("date") else "nodate"
    source_id = f"air-vn-{srn}-{date_str}"

    return Violation(
        source=SOURCE,
        source_id=source_id,
        facility_source_id=f"air-{srn}",
        facility_source=SOURCE,
        violation_type="Air Quality Violation Notice",
        violation_date=vio_date,
        statute=None,
        program_area="Air Quality",
        severity=_air_severity(rec),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
