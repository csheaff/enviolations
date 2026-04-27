"""Map raw Ohio EPA ArcGIS feature attributes to Pydantic models.

Ohio EPA data comes from ArcGIS REST API as JSON features with an
``attributes`` dict.  Datasets:
  - NPDES Select Facilities (water permits) → Facility
  - DMWM Regulated Facilities (waste management) → Facility
  - DERR Sites (environmental cleanup) → Facility
  - Spills2_OpenData (spill incidents) → Facility + Violation (15.8K)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, epoch_ms_to_date

SOURCE = "oh_epa"


def _collect_sic(attrs: dict) -> str | None:
    """Concatenate non-empty SIC codes from sic1..sic4 fields."""
    codes = []
    for key in ("sic1", "sic2", "sic3", "sic4"):
        val = clean(str(attrs[key])) if attrs.get(key) is not None else None
        if val:
            codes.append(val)
    return ",".join(codes) if codes else None


def map_npdes_facility(attrs: dict) -> Facility:
    """Convert NPDES_SELECT_FACS feature attributes to a Facility model.

    Key fields: ohio_epa_no, facility, facility_address, facility_city,
    facility_zip, facility_county, facility_latitude, facility_longitude,
    sic1-sic4, permit_fac_rating, district.
    """
    epa_no = clean(str(attrs["ohio_epa_no"])) if attrs.get("ohio_epa_no") is not None else ""

    return Facility(
        source=SOURCE,
        source_id=f"npdes-{epa_no}",
        name=clean(str(attrs["facility"])) if attrs.get("facility") is not None else "Unknown",
        address=clean(str(attrs["facility_address"])) if attrs.get("facility_address") is not None else None,
        city=clean(str(attrs["facility_city"])) if attrs.get("facility_city") is not None else None,
        state="OH",
        zip_code=clean(str(attrs["facility_zip"])) if attrs.get("facility_zip") is not None else None,
        county=clean(str(attrs["facility_county"])) if attrs.get("facility_county") is not None else None,
        lat=parse_float(attrs.get("facility_latitude")),
        lon=parse_float(attrs.get("facility_longitude")),
        naics_codes=None,
        sic_codes=_collect_sic(attrs),
        programs=clean(str(attrs["permit_fac_rating"])) if attrs.get("permit_fac_rating") is not None else "NPDES",
        last_updated=datetime.now(timezone.utc),
    )


def map_dmwm_facility(attrs: dict) -> Facility:
    """Convert DMWM_Regulated_Facilities feature attributes to a Facility model.

    Key fields: FP_PLACE_ID, PLACE_NAME, ADDR_1, ADDR_2, CITY, ST, ZIP_CODE,
    COUNTY_NAME, DD_LAT, DD_LON, PROGRAM_NAME, FACILITY_DESCRIPTION.
    """
    place_id = attrs.get("FP_PLACE_ID")
    place_id_str = str(int(place_id)) if place_id is not None else ""

    addr1 = clean(str(attrs["ADDR_1"])) if attrs.get("ADDR_1") is not None else None
    addr2 = clean(str(attrs["ADDR_2"])) if attrs.get("ADDR_2") is not None else None
    address = ", ".join(filter(None, [addr1, addr2])) if (addr1 or addr2) else None

    return Facility(
        source=SOURCE,
        source_id=f"dmwm-{place_id_str}",
        name=clean(str(attrs["PLACE_NAME"])) if attrs.get("PLACE_NAME") is not None else "Unknown",
        address=address,
        city=clean(str(attrs["CITY"])) if attrs.get("CITY") is not None else None,
        state=clean(str(attrs["ST"])) if attrs.get("ST") is not None else "OH",
        zip_code=clean(str(attrs["ZIP_CODE"])) if attrs.get("ZIP_CODE") is not None else None,
        county=clean(str(attrs["COUNTY_NAME"])) if attrs.get("COUNTY_NAME") is not None else None,
        lat=parse_float(attrs.get("DD_LAT")),
        lon=parse_float(attrs.get("DD_LON")),
        naics_codes=None,
        sic_codes=None,
        programs=clean(str(attrs["PROGRAM_NAME"])) if attrs.get("PROGRAM_NAME") is not None else None,
        last_updated=datetime.now(timezone.utc),
    )


def map_derr_site(attrs: dict) -> Facility:
    """Convert DERR_SITES_POINTS feature attributes to a Facility model.

    Key fields: derr_id, name, address, city, postalcode, county,
    latitude, longitude, activity, cerclis_id.
    """
    derr_id = attrs.get("derr_id")
    derr_id_str = str(int(derr_id)) if derr_id is not None else ""

    return Facility(
        source=SOURCE,
        source_id=f"derr-{derr_id_str}",
        name=clean(str(attrs["name"])) if attrs.get("name") is not None else "Unknown",
        address=clean(str(attrs["address"])) if attrs.get("address") is not None else None,
        city=clean(str(attrs["city"])) if attrs.get("city") is not None else None,
        state="OH",
        zip_code=clean(str(attrs["postalcode"])) if attrs.get("postalcode") is not None else None,
        county=clean(str(attrs["county"])) if attrs.get("county") is not None else None,
        lat=parse_float(attrs.get("latitude")),
        lon=parse_float(attrs.get("longitude")),
        naics_codes=None,
        sic_codes=None,
        programs=clean(str(attrs["activity"])) if attrs.get("activity") is not None else None,
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Spills2_OpenData (EmergResponse MapServer)
# ---------------------------------------------------------------------------

def _spill_date(attrs: dict) -> date | None:
    """Build a date from spillyear/spillmonth/spilldom fields."""
    year = attrs.get("spillyear")
    month = attrs.get("spillmonth")
    day = attrs.get("spilldom")
    if year and month and day:
        try:
            return date(int(year), int(month), int(day))
        except (ValueError, TypeError):
            pass
    # fallback to reporteddate (epoch-ms)
    return epoch_ms_to_date(attrs.get("reporteddate"))


def _spill_severity(amount, product: str | None) -> str | None:
    """Derive severity from spill amount and product type."""
    qty = parse_float(amount)
    prod = (product or "").lower()
    if any(h in prod for h in ("hazardous", "pcb", "radioactive")):
        return "High"
    if qty is not None and qty > 1000:
        return "High"
    if qty is not None and qty > 100:
        return "Medium"
    if qty is not None and qty > 0:
        return "Low"
    return None


def map_spill_facility(attrs: dict) -> Facility:
    """Convert a Spills2_OpenData feature to a Facility.

    Fields: casenumber, city_twn, county, latitude, longitude, oepadist.
    """
    case_num = clean(str(attrs["casenumber"])) if attrs.get("casenumber") is not None else ""

    return Facility(
        source=SOURCE,
        source_id=f"spill-{case_num}",
        name=clean(str(attrs.get("city_twn", ""))) + " Spill" if attrs.get("city_twn") else f"Spill {case_num}",
        address=None,
        city=clean(str(attrs["city_twn"])) if attrs.get("city_twn") is not None else None,
        state="OH",
        zip_code=None,
        county=clean(str(attrs["county"])) if attrs.get("county") is not None else None,
        lat=parse_float(attrs.get("latitude")),
        lon=parse_float(attrs.get("longitude")),
        naics_codes=None,
        sic_codes=None,
        programs="Spill",
        last_updated=datetime.now(timezone.utc),
    )


def map_spill_violation(attrs: dict) -> Violation:
    """Convert a Spills2_OpenData feature to a Violation.

    Fields: casenumber, reportedproduct, reportedamount, reporteduom,
    spillyear, spillmonth, spilldom, reporteddate, county, waterway.
    """
    case_num = clean(str(attrs["casenumber"])) if attrs.get("casenumber") is not None else ""
    product = clean(str(attrs["reportedproduct"]), sentinel=True) if attrs.get("reportedproduct") is not None else None

    desc_parts = []
    if product:
        desc_parts.append(product)
    amount = parse_float(attrs.get("reportedamount"))
    uom = clean(str(attrs.get("reporteduom", "")), sentinel=True) if attrs.get("reporteduom") else None
    if amount is not None and amount > 0:
        desc_parts.append(f"{amount:.0f} {uom or 'units'}")
    waterway = clean(str(attrs["waterway"]), sentinel=True) if attrs.get("waterway") is not None else None
    if waterway:
        desc_parts.append(f"Waterway: {waterway}")

    return Violation(
        source=SOURCE,
        source_id=f"spill-{case_num}",
        facility_source_id=f"spill-{case_num}",
        facility_source=SOURCE,
        violation_type="Spill",
        violation_date=_spill_date(attrs),
        statute=None,
        program_area="Emergency Response",
        severity=_spill_severity(attrs.get("reportedamount"), product),
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
