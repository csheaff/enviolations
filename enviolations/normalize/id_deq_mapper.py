"""Map raw Idaho DEQ ArcGIS feature data to Pydantic models.

Idaho DEQ data comes from the PCI (Potential Contaminant Inventory) layer
on SWA_PCI_WMS MapServer (Layer 12). This is a merged multi-agency dataset
with ~67K facilities covering UST, RCRA, NPDES, Superfund, Brownfield,
TRI, feedlots, mines, injection wells, and more.

Key attribute fields: FACILITYID, FACILITY, FAC_TYPE, ADDRESS, CITY,
ZIPCODE, COUNTY, STATE, SOURCE, LATITUDE, LONGITUDE, CONTAMINAN, DESCRIPTION.

Coordinates are in explicit LATITUDE/LONGITUDE fields (NAD83).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import parse_float, clean

SOURCE = "id_deq"

def map_pci_facility(attrs: dict) -> Facility:
    """Convert a PCI feature's attributes to a Facility model."""
    fac_id = attrs.get("FACILITYID")
    fac_id_str = str(int(fac_id)) if fac_id is not None and fac_id == fac_id else ""
    fac_type = clean(attrs.get("FAC_TYPE")) or ""

    # Build a prefixed source_id to avoid collisions across FAC_TYPEs
    prefix = fac_type.lower().replace(" ", "_")[:10] if fac_type else "pci"
    source_id = f"{prefix}-{fac_id_str}" if fac_id_str else f"{prefix}-{attrs.get('OBJECTID', '')}"

    programs = []
    if fac_type:
        programs.append(fac_type)
    contam = clean(attrs.get("CONTAMINAN"))
    if contam:
        programs.append(contam)

    zip_code = attrs.get("ZIPCODE")
    zip_str = str(int(zip_code)) if zip_code is not None and zip_code == zip_code else None
    if zip_str:
        zip_str = zip_str.zfill(5)

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name=clean(attrs.get("FACILITY")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state=clean(attrs.get("STATE")) or "ID",
        zip_code=zip_str,
        county=clean(attrs.get("COUNTY")),
        lat=parse_float(attrs.get("LATITUDE"), zero_as_none=True),
        lon=parse_float(attrs.get("LONGITUDE"), zero_as_none=True),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "PCI",
        last_updated=datetime.now(timezone.utc),
    )
