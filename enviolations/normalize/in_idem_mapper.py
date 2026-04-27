"""Map raw Indiana IDEM ArcGIS feature data to Pydantic models.

IN IDEM data comes from ArcGIS FeatureServer at gisdata.in.gov.
Facility datasets:
  - NPDES Facilities → Facility (permit_number key)
  - Underground Storage Tanks → Facility (regulatory_program_id key)
  - State Cleanup Sites → Facility (regulatory_program_id key)
  - Brownfields → Facility (bfd_no key)
  - Waste Disposal Storage and Handling → Facility (regulatory_program_id key)
Violation datasets:
  - Spills (IDEM_Land_Sites/FeatureServer/1700) → Facility + Violation (10.2K)
  - LUST Sites (IDEM_Land_Sites/FeatureServer/1600) → Facility + Violation (8.2K)

UST, Cleanup, Waste, Spills, and LUST share the same IDEM Central Registry schema.
Coordinates come from geometry objects (outSR=4326).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, extract_arcgis_coords, epoch_ms_to_date

SOURCE = "in_idem"


def map_npdes(feature: dict) -> Facility:
    """Convert an NPDES Facilities feature to a Facility model.

    Key fields: permit_number, facility_name, location_address, city, state,
    zip, county_name, latitude, longitude, permit_status, major_minor_status,
    sic_code.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    permit_number = clean(attrs.get("permit_number")) or ""
    permit_status = clean(attrs.get("permit_status"))
    major_minor = clean(attrs.get("major_minor_status"))
    programs = []
    if permit_status:
        programs.append(permit_status)
    if major_minor:
        programs.append(major_minor)

    sic = attrs.get("sic_code")
    sic_str = str(int(sic)) if sic is not None else None

    return Facility(
        source=SOURCE,
        source_id=f"npdes-{permit_number}",
        name=clean(attrs.get("facility_name")) or "Unknown",
        address=clean(attrs.get("location_address")),
        city=clean(attrs.get("city")),
        state=clean(attrs.get("state")) or "IN",
        zip_code=clean(attrs.get("zip")),
        county=clean(attrs.get("county_name")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=sic_str,
        programs=", ".join(programs) if programs else "NPDES",
        last_updated=datetime.now(timezone.utc),
    )


def map_registry_site(feature: dict, prefix: str, default_program: str) -> Facility:
    """Convert an IDEM Central Registry feature to a Facility model.

    Shared schema used by UST, State Cleanup, and Waste Disposal datasets.
    Key fields: regulatory_program_id, agency_interest_name, physical_address,
    municipality, zip_code, program, sub_program, latitude, longitude.
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    reg_id = clean(attrs.get("regulatory_program_id")) or ""
    program = clean(attrs.get("program"))
    sub_program = clean(attrs.get("sub_program"))
    programs = []
    if program:
        programs.append(program)
    if sub_program:
        programs.append(sub_program)

    return Facility(
        source=SOURCE,
        source_id=f"{prefix}-{reg_id}",
        name=clean(attrs.get("agency_interest_name")) or "Unknown",
        address=clean(attrs.get("physical_address")),
        city=clean(attrs.get("municipality")),
        state="IN",
        zip_code=clean(attrs.get("zip_code")),
        county=None,
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else default_program,
        last_updated=datetime.now(timezone.utc),
    )


def map_ust(feature: dict) -> Facility:
    """Convert a UST feature to a Facility model."""
    return map_registry_site(feature, "ust", "UST")


def map_cleanup(feature: dict) -> Facility:
    """Convert a State Cleanup Sites feature to a Facility model."""
    return map_registry_site(feature, "cleanup", "Cleanup")


def map_waste(feature: dict) -> Facility:
    """Convert a Waste Disposal Storage and Handling feature to a Facility model."""
    return map_registry_site(feature, "waste", "Waste Disposal")


def map_brownfield(feature: dict) -> Facility:
    """Convert a Brownfields feature to a Facility model.

    Key fields: bfd_no, site_names, address, city, county, status,
    x_coord (lon), y_coord (lat).
    """
    attrs = feature.get("attributes", {})
    lat, lon = extract_arcgis_coords(feature)

    bfd_no = clean(attrs.get("bfd_no")) or ""
    status = clean(attrs.get("status"))

    return Facility(
        source=SOURCE,
        source_id=f"bf-{bfd_no}",
        name=clean(attrs.get("site_names")) or "Unknown",
        address=clean(attrs.get("address")),
        city=clean(attrs.get("city")),
        state="IN",
        zip_code=None,
        county=clean(attrs.get("county")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=status or "Brownfield",
        last_updated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Spills & LUST (IDEM_Land_Sites/FeatureServer layers 1700 and 1600)
# ---------------------------------------------------------------------------

def map_spill_facility(feature: dict) -> Facility:
    """Convert a Spills feature to a Facility model."""
    return map_registry_site(feature, "spill", "Spill")


def map_spill_violation(feature: dict) -> Violation:
    """Convert a Spills feature to a Violation.

    Uses the IDEM Central Registry schema: agency_interest_id,
    agency_interest_name, reference_point, data_collection_date,
    program, sub_program.
    """
    attrs = feature.get("attributes", {})
    reg_id = clean(attrs.get("regulatory_program_id")) or ""

    desc_parts = []
    name = clean(attrs.get("agency_interest_name"))
    if name:
        desc_parts.append(name)
    ref_point = clean(attrs.get("reference_point"))
    if ref_point:
        desc_parts.append(f"Location: {ref_point}")
    sub_program = clean(attrs.get("sub_program"))
    if sub_program:
        desc_parts.append(f"Program: {sub_program}")

    return Violation(
        source=SOURCE,
        source_id=f"spill-{reg_id}",
        facility_source_id=f"spill-{reg_id}",
        facility_source=SOURCE,
        violation_type="Spill",
        violation_date=epoch_ms_to_date(attrs.get("data_collection_date")),
        statute=None,
        program_area="Emergency Response",
        severity="Medium",
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )


def map_lust_facility(feature: dict) -> Facility:
    """Convert a LUST Sites feature to a Facility model."""
    return map_registry_site(feature, "lust", "LUST")


def map_lust_violation(feature: dict) -> Violation:
    """Convert a LUST Sites feature to a Violation.

    Uses the IDEM Central Registry schema.
    """
    attrs = feature.get("attributes", {})
    reg_id = clean(attrs.get("regulatory_program_id")) or ""

    desc_parts = []
    name = clean(attrs.get("agency_interest_name"))
    if name:
        desc_parts.append(name)
    ref_point = clean(attrs.get("reference_point"))
    if ref_point:
        desc_parts.append(f"Location: {ref_point}")
    sub_program = clean(attrs.get("sub_program"))
    if sub_program:
        desc_parts.append(f"Program: {sub_program}")

    return Violation(
        source=SOURCE,
        source_id=f"lust-{reg_id}",
        facility_source_id=f"lust-{reg_id}",
        facility_source=SOURCE,
        violation_type="LUST Incident",
        violation_date=epoch_ms_to_date(attrs.get("data_collection_date")),
        statute=None,
        program_area="Storage Tanks",
        severity="Medium",
        description="; ".join(desc_parts) if desc_parts else None,
        last_updated=datetime.now(timezone.utc),
    )
