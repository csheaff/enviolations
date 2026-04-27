"""Connector for FL DEP (Florida Department of Environmental Protection).

Downloads JSON data from FL DEP's ArcGIS REST services at ca.dep.state.fl.us.
Four datasets:
  - WAFR Wastewater Facilities → facilities
  - ERIC Waste Cleanup Sites → facilities
  - CHAZ Hazardous Waste Facilities (LQGs) → facilities
  - Coastal Permit Violations (MapServer/11) → ~3.2K violations with coordinates

This source only covers Florida (state="FL").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.fl_dep_mapper import (
    map_chaz_facility,
    map_coastal_violation,
    map_coastal_violation_facility,
    map_eric_site,
    map_wafr_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints
_WAFR_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/WAFR/MapServer/0/query"
)
_ERIC_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/CLEANUP_SP/MapServer/8/query"
)
_CHAZ_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/CHAZ/MapServer/1/query"
)
_COASTAL_VIOLATIONS_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/COASTAL_ENV_PERM/MapServer/11/query"
)

class FLDEPSource(ArcGISSource):
    """Connector for FL DEP ArcGIS REST services."""

    name = "fl_dep"
    page_size = 1000

    def _query_attrs(self, url: str, label: str) -> list[dict]:
        """Query ArcGIS and return just the attributes dicts (no geometry)."""
        features = self._query_features(url, label, return_geometry=False)
        return [feat.get("attributes", {}) for feat in features]

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from FL DEP WAFR, ERIC, and CHAZ datasets.

        FL DEP only covers Florida. Returns empty for non-FL states.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP is Florida-only)", state)
            return

        seen_ids: set[str] = set()

        # WAFR Wastewater Facilities
        wafr_attrs = self._query_attrs(_WAFR_URL, "WAFR Wastewater Facilities")
        for attrs in wafr_attrs:
            try:
                facility = map_wafr_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping WAFR facility: %s", e)

        wafr_count = len(seen_ids)
        logger.info("Unique WAFR facilities: %s", wafr_count)

        # ERIC Waste Cleanup Sites
        eric_attrs = self._query_attrs(_ERIC_URL, "ERIC Waste Cleanup Sites")
        for attrs in eric_attrs:
            try:
                facility = map_eric_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping ERIC site: %s", e)

        eric_count = len(seen_ids) - wafr_count
        logger.info("Unique ERIC sites: %s", eric_count)

        # CHAZ Hazardous Waste (Large Quantity Generators)
        chaz_attrs = self._query_attrs(_CHAZ_URL, "CHAZ Hazardous Waste LQGs")
        for attrs in chaz_attrs:
            try:
                facility = map_chaz_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping CHAZ facility: %s", e)

        chaz_count = len(seen_ids) - wafr_count - eric_count
        logger.info("Unique CHAZ facilities: %s", chaz_count)

        # Coastal Permit Violations — create facility per violation
        cpv_features = self._query_features_with_geom(
            _COASTAL_VIOLATIONS_URL, "Coastal Permit Violations"
        )
        cpv_count = 0
        for feat in cpv_features:
            try:
                facility = map_coastal_violation_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    cpv_count += 1
            except Exception as e:
                logger.warning("Skipping coastal violation facility: %s", e)

        logger.info("Unique coastal violation facilities: %s", cpv_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def _query_features_with_geom(self, url: str, label: str) -> list[dict]:
        """Query ArcGIS with geometry returned (for coastal violations with coordinates)."""
        return self._query_features(url, label, return_geometry=True)

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP is Florida-only)", state)
            return

        # Coastal Permit Violations
        cpv_features = self._query_features_with_geom(
            _COASTAL_VIOLATIONS_URL, "Coastal Permit Violations"
        )
        count = 0
        for feat in cpv_features:
            try:
                violation = map_coastal_violation(feat)
                yield violation
                count += 1
            except Exception as e:
                logger.warning("Skipping coastal violation: %s", e)

        logger.info("Coastal permit violations: %s", count)

