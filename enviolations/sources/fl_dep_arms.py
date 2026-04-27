"""Connector for FL DEP ARMS (Air Resource Management System).

Downloads JSON data from FL DEP's ArcGIS REST services at ca.dep.state.fl.us.
One dataset:
  - ARMS/MapServer/0: Air-permitted facilities (~10.7K) → facilities

This source only covers Florida (state="FL").
No violations are available via ArcGIS (violations are web-only in AirInfo).
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..models import Facility, Violation
from ..normalize.fl_dep_arms_mapper import map_arms_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoint
_ARMS_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/ARMS/MapServer/0/query"
)


class FLDEPARMSSource(ArcGISSource):
    """Connector for FL DEP ARMS (Air Resource Management System)."""

    name = "fl_dep_arms"
    page_size = 1000

    @staticmethod
    def _has_id(source_id: str, prefix: str) -> bool:
        """True if source_id has a real ID after the prefix (not just 'arms-')."""
        return bool(source_id) and source_id != prefix

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from ARMS air-permitted facilities.

        FL DEP ARMS only covers Florida. Returns empty for non-FL states.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP ARMS is Florida-only)", state)
            return

        features = self._query_features(
            _ARMS_URL, "ARMS Facilities", return_geometry=True
        )
        seen_ids: set[str] = set()
        count = 0
        for feat in features:
            try:
                facility = map_arms_facility(feat)
                if (
                    self._has_id(facility.source_id, "arms-")
                    and facility.source_id not in seen_ids
                ):
                    seen_ids.add(facility.source_id)
                    yield facility
                    count += 1
            except Exception as e:
                logger.warning("Skipping ARMS facility: %s", e)

        logger.info("Unique ARMS facilities: %s", count)

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """ARMS has no violation data via ArcGIS. Returns empty iterator.

        FL DEP ARMS only covers Florida. Returns empty for non-FL states.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP ARMS is Florida-only)", state)
        # No violations available — AirInfo violations are web-only
        return
        yield  # makes this a generator returning empty iterator
