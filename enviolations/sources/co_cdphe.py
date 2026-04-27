"""Connector for CO CDPHE (Colorado Dept of Public Health and Environment) data.

Downloads JSON data from CDPHE ArcGIS MapServer services. Two datasets:
  - All Active Air Pollution Emitting Facilities (APCD) → facilities
  - Wastewater Treatment Plants (WQCD SWAP) → facilities

CDPHE does not publish a structured violation/enforcement dataset;
violations for Colorado are covered by federal EPA ECHO.

This source only covers Colorado (state="CO").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.co_cdphe_mapper import map_air_facility, map_wastewater_plant
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints
_AIR_URL = (
    "https://www.cohealthmaps.dphe.state.co.us/arcgis/rest/services"
    "/APCD/APCD_All_Active_Air_Pollution_Emitting_Facilities/MapServer/0/query"
)
_WASTEWATER_URL = (
    "https://www.cohealthmaps.dphe.state.co.us/arcgis/rest/services"
    "/WQCD/WQCD_SWAP_WASTEWATER_TREATEMENT_PLANTS/MapServer/0/query"
)

# Page sizes (air has 25K max, wastewater has 2K max)
_AIR_PAGE_SIZE = 5000
_WASTEWATER_PAGE_SIZE = 2000

class COCDPHESource(ArcGISSource):
    """Connector for Colorado CDPHE ArcGIS MapServer services."""

    name = "co_cdphe"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from CDPHE Air and Wastewater datasets.

        CDPHE only covers Colorado. Returns empty for non-CO states.
        """
        if state.upper() != "CO":
            logger.warning("Skipping %s (CDPHE is Colorado-only)", state)
            return

        seen_ids: set[str] = set()

        # Air Pollution Emitting Facilities (has Latitude/Longitude fields)
        air_features = self._query_features(
            _AIR_URL, "Air Pollution Emitting Facilities",
            return_geometry=False, page_size=_AIR_PAGE_SIZE,
        )
        for feat in air_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_air_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air facility: %s", e)

        air_count = len(seen_ids)
        logger.info("Unique air facilities: %s", air_count)

        # Wastewater Treatment Plants (geometry in Web Mercator, need outSR=4326)
        ww_features = self._query_features(
            _WASTEWATER_URL, "Wastewater Treatment Plants",
            return_geometry=True, page_size=_WASTEWATER_PAGE_SIZE,
        )
        for feat in ww_features:
            try:
                facility = map_wastewater_plant(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping wastewater plant: %s", e)

        ww_count = len(seen_ids) - air_count
        logger.info("Unique wastewater plants: %s", ww_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """CDPHE does not publish structured violation data.

        Violations for Colorado come from federal EPA ECHO (source='echo').
        """
        if state.upper() != "CO":
            logger.warning("Skipping %s (CDPHE is Colorado-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Colorado violations")
        return
        yield  # make this a generator

