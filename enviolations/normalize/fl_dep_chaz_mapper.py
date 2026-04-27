"""Map raw FL DEP CHAZ ArcGIS feature attributes to Pydantic models.

FL DEP Compliance and Hazardous Assessment (CHAZ) data comes from ArcGIS
REST API at ca.dep.state.fl.us. This mapper handles four datasets:
  - CHAZ/MapServer/0: Closed Hazardous Waste Facilities → Facility
  - CHAZ/MapServer/2: Small Quantity Generators (SQGs) → Facility
  - CHAZ/MapServer/4: Treatment, Storage & Disposal (TSDs) → Facility
  - CHAZ/MapServer/5: Compliance & Enforcement Tracking → Facility
    (full CHAZ facility master registry with DMS coordinates; NOT violations)

Coordinates for layers 0/2/4 are extracted from ArcGIS point geometry
(outSR=4326). Layer 5 uses DMS fields (LATITUDE_DD/MM/SS, LONGITUDE_DD/MM/SS).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, epoch_ms_to_date, extract_arcgis_coords, parse_float

SOURCE = "fl_dep_chaz"


def _dms_to_decimal(dd, mm, ss) -> float | None:
    """Convert degrees/minutes/seconds to decimal degrees."""
    d = parse_float(dd)
    m = parse_float(mm)
    s = parse_float(ss)
    if d is None:
        return None
    m = m or 0.0
    s = s or 0.0
    sign = -1 if d < 0 else 1
    return sign * (abs(d) + m / 60.0 + s / 3600.0)


def map_chaz_facility(
    feature_or_attrs: dict,
    *,
    default_program: str = "Hazardous Waste",
) -> Facility:
    """Convert a CHAZ facility feature (layers 0, 2, 4) to a Facility model.

    Accepts either a full ArcGIS feature dict (with ``attributes`` and
    optional ``geometry`` keys) or a bare attributes dict (for backwards
    compatibility with existing tests and callers that pass attrs directly).

    All three facility layers (Closed, SQG, TSD) share the same field
    structure: HANDLER_ID, NAME, ADDRESS, CITY, ZIP5, COUNTY_NAME,
    OFFICE, FAC_INS_TYPE.

    The ``default_program`` parameter is used as a fallback when
    FAC_INS_TYPE is empty, allowing the caller to distinguish
    Closed vs SQG vs TSD facilities.
    """
    # Support both full feature dicts and bare attribute dicts
    if "attributes" in feature_or_attrs:
        attrs = feature_or_attrs["attributes"]
        lat, lon = extract_arcgis_coords(feature_or_attrs)
    else:
        attrs = feature_or_attrs
        lat, lon = None, None

    handler_id = clean(attrs.get("HANDLER_ID")) or ""

    return Facility(
        source=SOURCE,
        source_id=f"chaz-{handler_id}",
        name=clean(attrs.get("NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="FL",
        zip_code=clean(attrs.get("ZIP5")),
        county=clean(attrs.get("COUNTY_NAME")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=clean(attrs.get("FAC_INS_TYPE")) or default_program,
        last_updated=datetime.now(timezone.utc),
    )


def map_chaz_layer5_facility(attrs: dict) -> Facility:
    """Convert a CHAZ Layer 5 (Compliance & Enforcement Tracking) record to a Facility.

    Layer 5 is the comprehensive CHAZ facility master registry (~47K records)
    covering ALL handler types (closed, SQG, TSD, LQG, transporter, etc.).
    Its field schema differs from layers 0/2/4:
      - Name: ME_NAME (not NAME)
      - Address: PHYS_ADDRESS_1 (not ADDRESS)
      - City: PHYS_CITY (not CITY)
      - Zip: PHYS_ZIP5 (not ZIP5)
      - Coordinates: DMS fields LATITUDE_DD/MM/SS, LONGITUDE_DD/MM/SS (not geometry)
      - Generator status: GENERATOR field
      - FAC_INS_TYPE: present (same field name as layers 0/2/4)
    """
    handler_id = clean(attrs.get("HANDLER_ID")) or ""

    lat = _dms_to_decimal(
        attrs.get("LATITUDE_DD"),
        attrs.get("LATITUDE_MM"),
        attrs.get("LATITUDE_SS"),
    )
    lon = _dms_to_decimal(
        attrs.get("LONGITUDE_DD"),
        attrs.get("LONGITUDE_MM"),
        attrs.get("LONGITUDE_SS"),
    )
    # FL longitudes are west — ensure negative
    if lon is not None and lon > 0:
        lon = -lon

    generator = clean(attrs.get("GENERATOR"))
    fac_ins_type = clean(attrs.get("FAC_INS_TYPE"))
    programs = fac_ins_type or (f"Generator: {generator}" if generator else "Hazardous Waste")

    return Facility(
        source=SOURCE,
        source_id=f"chaz-{handler_id}",
        name=clean(attrs.get("ME_NAME")) or "Unknown",
        address=clean(attrs.get("PHYS_ADDRESS_1")),
        city=clean(attrs.get("PHYS_CITY")),
        state="FL",
        zip_code=clean(attrs.get("PHYS_ZIP5")),
        county=clean(attrs.get("COUNTY_NAME")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_chaz_violation(attrs: dict) -> Violation:
    """Convert a CHAZ Compliance & Enforcement record (layer 5) to a Violation.

    Expected fields (defensive — all accessed via .get()):
      - HANDLER_ID: links to facility
      - OBJECTID or ENF_ID: unique enforcement action identifier
      - ACTIVITY_DATE / ENF_DATE / EVAL_DATE: enforcement action date (epoch ms)
      - ACTIVITY_TYPE / ENF_TYPE / EVAL_TYPE: type of enforcement action
      - ACTIVITY_STATUS / COMP_STATUS: compliance status
      - ACTIVITY_DESC / ENF_DESC / COMMENT_TEXT: description text
      - PENALTY_AMT: penalty amount

    Since the exact field names depend on the server schema (which can
    change), we try multiple plausible field names for each concept.
    """
    handler_id = clean(attrs.get("HANDLER_ID")) or ""

    # Unique enforcement ID — try several plausible fields
    enf_id = (
        clean(attrs.get("ENF_ID"))
        or clean(attrs.get("ACTIVITY_ID"))
        or clean(attrs.get("OBJECTID"))
        or ""
    )

    # Violation/enforcement date — try multiple date fields (epoch ms)
    vio_date = (
        epoch_ms_to_date(attrs.get("ACTIVITY_DATE"))
        or epoch_ms_to_date(attrs.get("ENF_DATE"))
        or epoch_ms_to_date(attrs.get("EVAL_DATE"))
        or epoch_ms_to_date(attrs.get("ACTUAL_RTC_DATE"))
        or epoch_ms_to_date(attrs.get("EVALUATION_START_DATE"))
    )

    # Violation/enforcement type
    vio_type = (
        clean(attrs.get("ACTIVITY_TYPE"))
        or clean(attrs.get("ENF_TYPE"))
        or clean(attrs.get("EVAL_TYPE"))
        or clean(attrs.get("VIOLATION_TYPE"))
        or "Compliance/Enforcement"
    )

    # Compliance status
    status = (
        clean(attrs.get("ACTIVITY_STATUS"))
        or clean(attrs.get("COMP_STATUS"))
        or clean(attrs.get("COMPLIANCE_STATUS"))
        or clean(attrs.get("RETURN_TO_COMPLIANCE"))
    )

    # Description parts
    desc_parts = []
    desc = (
        clean(attrs.get("ACTIVITY_DESC"))
        or clean(attrs.get("ENF_DESC"))
        or clean(attrs.get("COMMENT_TEXT"))
        or clean(attrs.get("VIOLATION_DESC"))
    )
    if desc:
        desc_parts.append(desc)
    if status:
        desc_parts.append(f"Status: {status}")

    penalty = clean(attrs.get("PENALTY_AMT"))
    if penalty:
        desc_parts.append(f"Penalty: ${penalty}")

    # Severity from penalty presence
    severity = None
    if penalty:
        try:
            if float(penalty) > 0:
                severity = "Penalty Assessed"
        except (ValueError, TypeError):
            pass

    return Violation(
        source=SOURCE,
        source_id=f"chaz-enf-{enf_id}" if enf_id else f"chaz-enf-{handler_id}",
        facility_source_id=f"chaz-{handler_id}",
        facility_source=SOURCE,
        violation_type=vio_type,
        violation_date=vio_date,
        statute=None,
        program_area="Hazardous Waste",
        severity=severity,
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
