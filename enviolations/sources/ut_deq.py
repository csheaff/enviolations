"""Connector for UT DEQ (Utah Department of Environmental Quality) data.

Downloads JSON data from Utah DEQ's ArcGIS Online FeatureServer via AGOL.
Three facility datasets:
  - FacilityUST (FeatureServer/0): ~6,325 underground storage tank facilities
  - DAQAirEmissionsInventory (FeatureServer/0): ~1,729 air emissions facilities
  - TIER2 (FeatureServer/0): ~7,733 hazardous chemical (EPCRA) facilities

UT DEQ does not publish structured violation/enforcement data via ArcGIS;
violations for Utah are covered by federal EPA ECHO.

This source only covers Utah (state="UT").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ut_deq_mapper import map_air_facility, map_tier2_facility, map_ust_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services"

_UST_URL = f"{_BASE}/FacilityUST/FeatureServer/0/query"
_AIR_URL = f"{_BASE}/DAQAirEmissionsInventory/FeatureServer/0/query"
_TIER2_URL = f"{_BASE}/TIER2/FeatureServer/0/query"

class UTDEQSource(ArcGISSource):
    """Connector for Utah DEQ ArcGIS Online FeatureServer."""

    name = "ut_deq"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "UT":
            logger.warning("Skipping %s (UT DEQ is Utah-only)", state)
            return

        seen_ids: set[str] = set()

        # Underground Storage Tanks
        ust_features = self._query_features(_UST_URL, "UST Facilities")
        for feat in ust_features:
            try:
                facility = map_ust_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST facility: %s", e)

        ust_count = len(seen_ids)
        logger.info("Unique UST facilities: %s", ust_count)

        # Air Emissions Inventory
        air_features = self._query_features(_AIR_URL, "Air Emissions Inventory")
        for feat in air_features:
            try:
                facility = map_air_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air facility: %s", e)

        air_count = len(seen_ids) - ust_count
        logger.info("Unique air facilities: %s", air_count)

        # TIER2 Hazardous Chemical Facilities
        tier2_features = self._query_features(_TIER2_URL, "TIER2 Facilities")
        for feat in tier2_features:
            try:
                facility = map_tier2_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping TIER2 facility: %s", e)

        tier2_count = len(seen_ids) - ust_count - air_count
        logger.info("Unique TIER2 facilities: %s", tier2_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "UT":
            logger.warning("Skipping %s (UT DEQ is Utah-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Utah violations")
        return
        yield  # make this a generator

