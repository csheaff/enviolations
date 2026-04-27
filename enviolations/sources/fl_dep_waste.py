"""Connector for FL DEP Solid Waste and Institutional Controls Registry.

Downloads JSON data from FL DEP's ArcGIS REST services at ca.dep.state.fl.us.
Two datasets on the DWM_WASTE_ICR_BACKG service:
  - MapServer/1: Solid Waste Facilities (~13,586) → facilities
  - MapServer/12: Institutional Controls Registry (~2,584) → facilities

This source only covers Florida (state="FL").
No violations are available — facilities only.
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..models import Facility, Violation
from ..normalize.fl_dep_waste_mapper import map_icr_facility, map_solid_waste_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints
_SOLID_WASTE_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/DWM_WASTE_ICR_BACKG/MapServer/1/query"
)
_ICR_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/DWM_WASTE_ICR_BACKG/MapServer/12/query"
)


class FLDEPWasteSource(ArcGISSource):
    """Connector for FL DEP Solid Waste and Institutional Controls Registry."""

    name = "fl_dep_waste"
    page_size = 1000

    def _query_attrs(self, url: str, label: str) -> list[dict]:
        """Query ArcGIS and return just the attributes dicts (no geometry)."""
        features = self._query_features(url, label, return_geometry=False)
        return [feat.get("attributes", {}) for feat in features]

    @staticmethod
    def _has_id(source_id: str, prefix: str) -> bool:
        """True if source_id has a real ID after the prefix."""
        return bool(source_id) and source_id != prefix

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from Solid Waste and ICR datasets.

        FL DEP Waste only covers Florida. Returns empty for non-FL states.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP Waste is Florida-only)", state)
            return

        seen_ids: set[str] = set()

        # Solid Waste Facilities (DMS coords in attributes, no geometry needed)
        sw_attrs = self._query_attrs(_SOLID_WASTE_URL, "Solid Waste Facilities")
        for attrs in sw_attrs:
            try:
                facility = map_solid_waste_facility(attrs)
                if (
                    self._has_id(facility.source_id, "swaste-")
                    and facility.source_id not in seen_ids
                ):
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping solid waste facility: %s", e)

        sw_count = len(seen_ids)
        logger.info("Unique solid waste facilities: %s", sw_count)

        # Institutional Controls Registry (may have geometry)
        icr_features = self._query_features(
            _ICR_URL, "Institutional Controls Registry", return_geometry=True
        )
        for feat in icr_features:
            try:
                facility = map_icr_facility(feat)
                if (
                    self._has_id(facility.source_id, "icr-")
                    and facility.source_id not in seen_ids
                ):
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping ICR facility: %s", e)

        icr_count = len(seen_ids) - sw_count
        logger.info("Unique ICR facilities: %s", icr_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """No violation data available. Returns empty iterator.

        FL DEP Waste only covers Florida. Returns empty for non-FL states.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP Waste is Florida-only)", state)
        # No violations available — solid waste + institutional controls only
        return
        yield  # makes this a generator returning empty iterator
