"""Connector for VA DEQ (Virginia Department of Environmental Quality) data.

Downloads JSON data from Virginia DEQ's EDMA ArcGIS MapServer at
apps.deq.virginia.gov. Five datasets:
  - Active Air Sites (Layer 294) → facilities
  - Solid Waste Permits (Layer 100) → facilities
  - Registered Petroleum Tank Facilities (Layer 102) → facilities
  - Petroleum Releases (Layer 104) → facilities + violations (53K confirmed releases)
  - PReP Reports (Layer 175) → facilities + violations (28K pollution incidents)

This source only covers Virginia (state="VA").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.va_deq_mapper import (
    map_air_site, map_solid_waste, map_petroleum_tank,
    map_release_facility, map_release_violation,
    map_prep_facility, map_prep_violation, has_prep_violation,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API base (EDMA MapServer)
_EDMA_BASE = (
    "https://apps.deq.virginia.gov/arcgis/rest/services"
    "/public/EDMA/MapServer"
)
_AIR_URL = f"{_EDMA_BASE}/294/query"
_SOLID_WASTE_URL = f"{_EDMA_BASE}/100/query"
_PETROLEUM_URL = f"{_EDMA_BASE}/102/query"
_RELEASES_URL = f"{_EDMA_BASE}/104/query"
_PREP_URL = f"{_EDMA_BASE}/175/query"

class VADEQSource(ArcGISSource):
    """Connector for Virginia DEQ EDMA ArcGIS MapServer."""

    name = "va_deq"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from VA DEQ Air, Solid Waste, and Petroleum Tank datasets.

        VA DEQ only covers Virginia. Returns empty for non-VA states.
        """
        if state.upper() != "VA":
            logger.warning("Skipping %s (VA DEQ is Virginia-only)", state)
            return

        seen_ids: set[str] = set()

        # Active Air Sites (need geometry for lat/lon)
        air_features = self._query_features(_AIR_URL, "Active Air Sites", return_geometry=True)
        for feat in air_features:
            try:
                facility = map_air_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air site: %s", e)

        air_count = len(seen_ids)
        logger.info("Unique air sites: %s", air_count)

        # Solid Waste Permits (need geometry for lat/lon)
        sw_features = self._query_features(_SOLID_WASTE_URL, "Solid Waste Permits", return_geometry=True)
        for feat in sw_features:
            try:
                facility = map_solid_waste(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping solid waste permit: %s", e)

        sw_count = len(seen_ids) - air_count
        logger.info("Unique solid waste permits: %s", sw_count)

        # Petroleum Tank Facilities (has explicit Lat/Lon fields)
        pet_features = self._query_features(_PETROLEUM_URL, "Petroleum Tank Facilities")
        for feat in pet_features:
            try:
                facility = map_petroleum_tank(feat.get("attributes", feat))
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping petroleum tank: %s", e)

        pet_count = len(seen_ids) - air_count - sw_count
        logger.info("Unique petroleum tank facilities: %s", pet_count)

        # Petroleum Releases (Layer 104) — create facility per release site
        prev_count = len(seen_ids)
        release_features = self._query_features(
            _RELEASES_URL, "Petroleum Releases", return_geometry=True
        )
        self._release_features = release_features  # cache for fetch_violations
        for feat in release_features:
            try:
                facility = map_release_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping release facility: %s", e)

        rel_count = len(seen_ids) - prev_count
        logger.info("Unique release site facilities: %s", rel_count)

        # PReP Reports (Layer 175) — create facility per pollution incident
        prev_count = len(seen_ids)
        prep_features = self._query_features(
            _PREP_URL, "PReP Reports", return_geometry=True
        )
        self._prep_features = prep_features  # cache for fetch_violations
        for feat in prep_features:
            if not has_prep_violation(feat):
                continue
            try:
                facility = map_prep_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping PReP facility: %s", e)

        prep_count = len(seen_ids) - prev_count
        logger.info("Unique PReP incident facilities: %s", prep_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from Petroleum Releases and PReP Reports.

        Uses cached features from fetch_facilities() if available,
        otherwise fetches them fresh.
        """
        if state.upper() != "VA":
            logger.warning("Skipping %s (VA DEQ is Virginia-only)", state)
            return

        # Petroleum Releases (all are confirmed violations)
        release_features = getattr(self, "_release_features", None)
        if release_features is None:
            release_features = self._query_features(
                _RELEASES_URL, "Petroleum Releases", return_geometry=True
            )

        release_count = 0
        for feat in release_features:
            try:
                violation = map_release_violation(feat)
                if violation.source_id:
                    yield violation
                    release_count += 1
            except Exception as e:
                logger.warning("Skipping release violation: %s", e)

        logger.info("Petroleum release violations: %s", release_count)

        # PReP Reports (filtered — exclude "no pollution observed")
        prep_features = getattr(self, "_prep_features", None)
        if prep_features is None:
            prep_features = self._query_features(
                _PREP_URL, "PReP Reports", return_geometry=True
            )

        prep_count = 0
        prep_filtered = 0
        for feat in prep_features:
            if not has_prep_violation(feat):
                prep_filtered += 1
                continue
            try:
                violation = map_prep_violation(feat)
                if violation.source_id:
                    yield violation
                    prep_count += 1
            except Exception as e:
                logger.warning("Skipping PReP violation: %s", e)

        logger.info("PReP violations: %s (filtered %s non-violations)", prep_count, prep_filtered)
        logger.info("Total violations: %s", release_count + prep_count)

