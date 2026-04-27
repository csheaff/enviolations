"""Map raw Nebraska DEE data to Pydantic models.

Two data sources:

1. ArcGIS MapServer (deqmaps.nebraska.gov):
   - DEQ Coordinates (layer 0): cid, programs, facid + point geometry
     Programs are colon-delimited codes like :RCR:PCS:AIR:TL3:SF:
   Note: This dataset only has coordinates, program codes, and IDs.
   No facility names, addresses, or other details are available.

2. LUST/Spill database CSV (deq-iis.ne.gov):
   - ~21K petroleum release/spill incident records
   - Fields: NDEQ File #, Facility Name, Address, City, County,
     Discovery Date, Material Released, Incident Type, Status, etc.

Program code meanings:
  RCR = RCRA (hazardous waste)
  PCS = Permit Compliance System
  AIR = Air permits
  TL3 = Title III (SARA/EPCRA)
  SF  = Superfund
  RA  = Remedial Action
  RAP = Remedial Action Plan
  IWM = Integrated Waste Management
  UIC = Underground Injection Control
  LST = Leaking Storage Tank
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import parse_float, clean

SOURCE = "ne_dee"

# Map program codes to human-readable names
_PROGRAM_NAMES = {
    "RCR": "RCRA",
    "PCS": "Permit Compliance",
    "AIR": "Air",
    "TL3": "Title III/EPCRA",
    "SF": "Superfund",
    "RA": "Remedial Action",
    "RAP": "Remedial Action Plan",
    "IWM": "Integrated Waste",
    "UIC": "Underground Injection",
    "LST": "Leaking Storage Tank",
}


def _parse_programs(raw: str | None) -> str:
    """Parse colon-delimited program codes like ':RCR:PCS:AIR:' to readable string."""
    if not raw:
        return "Regulated Facility"
    codes = [c.strip() for c in raw.split(":") if c.strip()]
    if not codes:
        return "Regulated Facility"
    names = [_PROGRAM_NAMES.get(c, c) for c in codes]
    return ", ".join(names)

def map_facility(feature: dict) -> Facility:
    """Convert a DEQ coordinates feature to a Facility."""
    attrs = feature.get("attributes", {})

    facid = clean(attrs.get("facid")) or ""
    cid = clean(attrs.get("cid")) or ""
    source_id = f"fac-{facid}" if facid else f"cid-{cid or attrs.get('FID', '')}"

    # Coordinates come from geometry (requested as WGS84 via outSR=4326)
    lat, lon = None, None
    geom = feature.get("geometry")
    if geom:
        lon = parse_float(geom.get("x"), zero_as_none=True)
        lat = parse_float(geom.get("y"), zero_as_none=True)

    programs = _parse_programs(clean(attrs.get("programs")))

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=f"NE Facility {facid}" if facid else "Unknown",
        address=None,
        city=None,
        state="NE",
        zip_code=None,
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )

def _parse_lust_date(val: str | None) -> date | None:
    """Parse date from LUST CSV — handles MM/DD/YYYY, M/D/YYYY, YYYY-MM-DD."""
    if not val:
        return None
    val = val.strip()
    if not val:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None

def _lust_severity(status: str | None) -> str | None:
    """Map LUST Status field to a severity value."""
    if not status:
        return None
    s = status.strip()
    if not s:
        return None
    # Active/open statuses
    if s in ("Active investigation", "Active investigation - prioritized",
             "Active investigation - new"):
        return "Active"
    # Backlogged / deferred
    if s.startswith("Backlog") or s.startswith("Deferred"):
        return "Backlogged"
    # Closed / resolved
    if s in ("No further action", "No further action required",
             "Closed", "Completed"):
        return "Resolved"
    # Return the raw status for anything else
    return s

def map_lust_facility(row: dict) -> Facility:
    """Create a Facility from a LUST/spill CSV row.

    These facilities don't have lat/lon, but have name, address, city, and
    county. The entity resolution system may match them to existing NE DEE
    ArcGIS facilities by address.

    The LUST CSV has no facility ZIP code field. We use the owner's mailing
    ZIP as a fallback only when the owner is in Nebraska (to avoid assigning
    out-of-state ZIPs to Nebraska facilities).
    """
    ndeq_file = (row.get("NDEQ File #") or "").strip()
    if not ndeq_file:
        raise ValueError("Missing NDEQ File #")

    # Use owner ZIP only when owner is in NE (facility ZIP not in source)
    owner_state = clean(row.get("Owner Mailing State"))
    owner_zip = clean(row.get("Owner Zip Code ID"))
    zip_code = owner_zip if owner_state == "NE" and owner_zip else None

    return Facility(
        source=SOURCE,
        source_id=f"lust-{ndeq_file}",
        name=clean(row.get("Facility Name")) or "Unknown",
        address=clean(row.get("Facility Address")),
        city=clean(row.get("Facility City")),
        state="NE",
        zip_code=zip_code,
        county=clean(row.get("Facility County")),
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs="Leaking Storage Tank",
        last_updated=datetime.now(timezone.utc),
    )

def map_lust_spill(row: dict) -> Violation:
    """Convert a LUST/spill CSV row to a Violation model.

    CSV headers: 'NDEQ File #', 'Facility Name', 'Facility Address',
    'Facility City', 'Facility County', 'Discovery Date',
    'Material Released', 'Incident Type', 'Status',
    'State Fire Marshal', 'Owner', 'Owner Mailing Address',
    'Owner City', 'Owner State', 'Owner Zip'
    """
    ndeq_file = (row.get("NDEQ File #") or "").strip()
    if not ndeq_file:
        raise ValueError("Missing NDEQ File #")

    material = clean(row.get("Material Released")) or "Unknown material"
    facility_name = clean(row.get("Facility Name")) or "Unknown facility"
    city = clean(row.get("Facility City")) or "Unknown city"
    incident_type = clean(row.get("Incident Type")) or "Petroleum Release"
    status = clean(row.get("Status"))

    return Violation(
        source=SOURCE,
        source_id=f"lust-{ndeq_file}",
        facility_source_id=f"lust-{ndeq_file}",
        facility_source=SOURCE,
        violation_type=incident_type,
        violation_date=_parse_lust_date(row.get("Discovery Date")),
        statute=None,
        program_area="LUST",
        severity=_lust_severity(status),
        description=f"{material} release at {facility_name}, {city}",
        last_updated=datetime.now(timezone.utc),
    )
