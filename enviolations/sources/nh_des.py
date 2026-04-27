"""Connector for NH DES (New Hampshire Department of Environmental Services) data.

Downloads JSON data from NH DES's ArcGIS FeatureServer at gis.des.nh.gov.
Four facility datasets from DES_Data_Public:
  - Air Facility Systems (Layer 1): ~308 air-permitted facilities
  - Hazardous Waste Generators (Layer 7): ~7,493 RCRA facilities
  - Underground Storage Tank Sites (Layer 13): ~9,230 UST locations
  - Solid Waste Facilities (Layer 12): ~683 landfills/transfer stations

NH DES does not publish structured violation/enforcement data via ArcGIS;
violations for New Hampshire are covered by federal EPA ECHO.

This source only covers New Hampshire (state="NH").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.nh_des_mapper import map_air_facility, map_hazwaste, map_solid_waste, map_ust
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://gis.des.nh.gov/server/rest/services/Core_GIS_Datasets/DES_Data_Public/FeatureServer"

_AIR_URL = f"{_BASE}/1/query"
_HAZWASTE_URL = f"{_BASE}/7/query"
_UST_URL = f"{_BASE}/13/query"
_SOLIDWASTE_URL = f"{_BASE}/12/query"

class NHDESSource(ArcGISSource):
    """Connector for New Hampshire DES ArcGIS FeatureServer."""

    name = "nh_des"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "NH":
            logger.warning("Skipping %s (NH DES is New Hampshire-only)", state)
            return

        seen_ids: set[str] = set()

        # Air Facility Systems
        air_features = self._query_features(_AIR_URL, "Air Facility Systems")
        for feat in air_features:
            try:
                facility = map_air_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air facility: %s", e)

        air_count = len(seen_ids)
        logger.info("Unique air facilities: %s", air_count)

        # Hazardous Waste Generators
        hw_features = self._query_features(_HAZWASTE_URL, "Hazardous Waste Generators")
        for feat in hw_features:
            try:
                facility = map_hazwaste(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping hazwaste facility: %s", e)

        hw_count = len(seen_ids) - air_count
        logger.info("Unique hazwaste facilities: %s", hw_count)

        # Underground Storage Tanks
        ust_features = self._query_features(_UST_URL, "UST Sites")
        for feat in ust_features:
            try:
                facility = map_ust(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST site: %s", e)

        ust_count = len(seen_ids) - air_count - hw_count
        logger.info("Unique UST sites: %s", ust_count)

        # Solid Waste Facilities
        sw_features = self._query_features(_SOLIDWASTE_URL, "Solid Waste Facilities")
        for feat in sw_features:
            try:
                facility = map_solid_waste(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping solid waste facility: %s", e)

        sw_count = len(seen_ids) - air_count - hw_count - ust_count
        logger.info("Unique solid waste facilities: %s", sw_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "NH":
            logger.warning("Skipping %s (NH DES is New Hampshire-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for New Hampshire violations")
        return
        yield  # make this a generator

