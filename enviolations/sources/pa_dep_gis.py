"""Connector for PA DEP GIS (Pennsylvania DEP PASDA ArcGIS facility layers).

Downloads facility data from PASDA (Pennsylvania Spatial Data Access) ArcGIS
REST services at mapservices.pasda.psu.edu. Seven layers from the DEP MapServer:

  Group 1 — Storage Tanks:
    - Layer 27: Storage Tanks Active (~11K)

  Group 2 — Cleanup / Remediation:
    - Layer 18: Land Recycling Cleanup Locations (~23K)

  Group 3 — Hazardous Waste:
    - Layer 5: Captive Hazardous Waste Operations (~5K)
    - Layer 9: Commercial Hazardous Waste Operations (~65)

  Group 4 — Solid Waste:
    - Layer 20: Municipal Waste Operations (~3K)
    - Layer 26: Residual Waste Operations (~2K)

  Group 5 — Abandoned Mine Lands:
    - Layer 0: AML Inventory Points (~13K)

This source only covers Pennsylvania (state="PA").
No violations are available via these ArcGIS layers.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterator

from ..models import Facility, Violation
from ..normalize.pa_dep_gis_mapper import (
    map_aml_facility,
    map_cleanup_facility,
    map_hwcap_facility,
    map_hwcom_facility,
    map_mwaste_facility,
    map_rwaste_facility,
    map_tank_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API base — layer ID inserted at runtime
_BASE_URL = (
    "https://mapservices.pasda.psu.edu/server/rest/services"
    "/pasda/DEP/MapServer/{layer}/query"
)

# Layer configuration: (layer_id, label, mapper_function, id_prefix)
# id_prefix is used by _has_id to filter empty source_ids.
_LAYERS: list[tuple[int, str, Callable[[dict], Facility], str]] = [
    (27, "Storage Tanks Active", map_tank_facility, "tank-"),
    (18, "Land Recycling Cleanup", map_cleanup_facility, "cleanup-"),
    (5, "Captive Hazardous Waste", map_hwcap_facility, "hwcap-"),
    (9, "Commercial Hazardous Waste", map_hwcom_facility, "hwcom-"),
    (20, "Municipal Waste Operations", map_mwaste_facility, "mwaste-"),
    (26, "Residual Waste Operations", map_rwaste_facility, "rwaste-"),
    (0, "AML Inventory Points", map_aml_facility, "aml-"),
]


class PADEPGISSource(ArcGISSource):
    """Connector for PA DEP PASDA ArcGIS facility layers."""

    name = "pa_dep_gis"
    page_size = 1000

    @staticmethod
    def _has_id(source_id: str, prefix: str) -> bool:
        """True if source_id has a real ID after the prefix."""
        return bool(source_id) and source_id != prefix

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from all seven PA DEP GIS layers.

        PA DEP GIS only covers Pennsylvania. Returns empty for non-PA states.
        """
        if state.upper() != "PA":
            logger.warning("Skipping %s (PA DEP GIS is Pennsylvania-only)", state)
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

        logger.info("Total unique PA DEP GIS facilities: %s", total)

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """No violation data available from PA DEP GIS layers.

        PA DEP GIS only covers Pennsylvania. Returns empty for non-PA states.
        """
        if state.upper() != "PA":
            logger.warning("Skipping %s (PA DEP GIS is Pennsylvania-only)", state)
        # No violations available from these facility layers
        return
        yield  # makes this a generator returning empty iterator
