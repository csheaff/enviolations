"""Connector for NY DEC GIS (New York DEC ArcGIS facility layers).

Downloads facility data from NY DEC's ArcGIS REST services at
gisservices.dec.ny.gov. Twelve layers from the dil_permits_and_regs MapServer:

  Group 1 — Storage:
    - Layer 21: Petroleum Bulk Storage Facilities (~67K)
    - Layer 23: Chemical Bulk Storage Facilities (~3.9K)
    - Layer 22: Major Oil Storage Facilities (~161)

  Group 2 — Air:
    - Layer 3: Air Facility Registrations (~7.4K)
    - Layer 4: Air Permits Title V (~294)
    - Layer 5: Air Permits State (~623)

  Group 3 — Hazardous Waste / Landfills:
    - Layer 12: Inactive Solid Waste Landfills (~1.9K)
    - Layer 6: Hazardous Waste Generators (~202)
    - Layer 2: Hazardous Waste TSD Facilities (~26)

  Group 4 — Water / Mining:
    - Layer 18: SPDES Wastewater Facilities (~1.6K)
    - Layer 20: MSGP Industrial Stormwater (~2.4K)
    - Layer 24: Permitted Mines (~5K)

This source only covers New York (state="NY").
No violations are available via these ArcGIS layers.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterator

from ..models import Facility, Violation
from ..normalize.ny_dec_gis_mapper import (
    map_air_reg_facility,
    map_air_state_facility,
    map_air_titlev_facility,
    map_cbs_facility,
    map_hwgen_facility,
    map_hwtsd_facility,
    map_landfill_facility,
    map_mine_facility,
    map_mosf_facility,
    map_msgp_facility,
    map_pbs_facility,
    map_spdes_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API base — layer ID inserted at runtime
_BASE_URL = (
    "https://gisservices.dec.ny.gov/arcgis/rest/services"
    "/dil_permits_and_regs/MapServer/{layer}/query"
)

# Layer configuration: (layer_id, label, mapper_function, id_prefix)
# id_prefix is used by _has_id to filter empty source_ids.
_LAYERS: list[tuple[int, str, Callable[[dict], Facility], str]] = [
    (21, "Petroleum Bulk Storage", map_pbs_facility, "pbs-"),
    (23, "Chemical Bulk Storage", map_cbs_facility, "cbs-"),
    (22, "Major Oil Storage", map_mosf_facility, "mosf-"),
    (3, "Air Facility Registrations", map_air_reg_facility, "air-"),
    (4, "Air Permits Title V", map_air_titlev_facility, "airv-"),
    (5, "Air Permits State", map_air_state_facility, "airs-"),
    (12, "Inactive Landfills", map_landfill_facility, "landfill-"),
    (6, "Hazardous Waste Generators", map_hwgen_facility, "hwgen-"),
    (2, "Hazardous Waste TSD", map_hwtsd_facility, "hwtsd-"),
    (18, "SPDES Wastewater", map_spdes_facility, "spdes-"),
    (20, "MSGP Industrial Stormwater", map_msgp_facility, "msgp-"),
    (24, "Permitted Mines", map_mine_facility, "mine-"),
]


class NYDECGISSource(ArcGISSource):
    """Connector for NY DEC ArcGIS facility layers."""

    name = "ny_dec_gis"
    page_size = 1000

    @staticmethod
    def _has_id(source_id: str, prefix: str) -> bool:
        """True if source_id has a real ID after the prefix."""
        return bool(source_id) and source_id != prefix

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from all nine NY DEC GIS layers.

        NY DEC GIS only covers New York. Returns empty for non-NY states.
        """
        if state.upper() != "NY":
            logger.warning("Skipping %s (NY DEC GIS is New York-only)", state)
            return

        seen_ids: set[str] = set()
        total = 0

        for layer_id, label, mapper, prefix in _LAYERS:
            url = _BASE_URL.format(layer=layer_id)
            try:
                features = self._query_features(
                    url, label, return_geometry=True
                )
            except Exception as e:
                logger.error("Failed to fetch %s (layer %s): %s", label, layer_id, e)
                continue

            layer_count = 0
            for feat in features:
                try:
                    facility = mapper(feat)
                    if (
                        self._has_id(facility.source_id, prefix)
                        and facility.source_id not in seen_ids
                    ):
                        seen_ids.add(facility.source_id)
                        yield facility
                        layer_count += 1
                except Exception as e:
                    logger.warning("Skipping %s feature: %s", label, e)

            total += layer_count
            logger.info("Unique %s facilities: %s", label, layer_count)

        logger.info("Total unique NY DEC GIS facilities: %s", total)

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """No violation data available from NY DEC GIS layers.

        NY DEC GIS only covers New York. Returns empty for non-NY states.
        """
        if state.upper() != "NY":
            logger.warning("Skipping %s (NY DEC GIS is New York-only)", state)
        # No violations available from these facility layers
        return
        yield  # makes this a generator returning empty iterator
