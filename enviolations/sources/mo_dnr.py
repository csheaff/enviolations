"""Connector for MO DNR (Missouri Department of Natural Resources) data.

Downloads JSON data from MO DNR's ArcGIS REST services at gis.dnr.mo.gov.
Five facility datasets:
  - Hazardous Waste Generators (waste/haz_waste_generators/MapServer/0)
  - Air Facility Locations (air/air_facility_locations/MapServer/0)
  - Public Drinking Water Systems (water/PubDrinkingWaterSystems/MapServer/0)
  - E-Start Cleanup Sites (e_start/e_start/MapServer/0)
  - UST Facilities (e_start/e_start/MapServer/3)

MO DNR does not publish structured violation/enforcement data via ArcGIS;
violations for Missouri are covered by federal EPA ECHO.

This source only covers Missouri (state="MO").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.mo_dnr_mapper import (
    map_air_facility,
    map_cleanup_site,
    map_drinking_water,
    map_haz_waste,
    map_ust,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API base
_BASE = "https://gis.dnr.mo.gov/host/rest/services"
_HAZ_WASTE_URL = f"{_BASE}/waste/haz_waste_generators/MapServer/0/query"
_AIR_URL = f"{_BASE}/air/air_facility_locations/MapServer/0/query"
_WATER_URL = f"{_BASE}/water/PubDrinkingWaterSystems/MapServer/0/query"
_CLEANUP_URL = f"{_BASE}/e_start/e_start/MapServer/0/query"
_UST_URL = f"{_BASE}/e_start/e_start/MapServer/3/query"

class MODNRSource(ArcGISSource):
    """Connector for Missouri DNR ArcGIS REST services."""

    name = "mo_dnr"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from MO DNR hazwaste, air, water, cleanup, and UST datasets.

        MO DNR only covers Missouri. Returns empty for non-MO states.
        """
        if state.upper() != "MO":
            logger.warning("Skipping %s (MO DNR is Missouri-only)", state)
            return

        seen_ids: set[str] = set()

        # Hazardous Waste Generators
        hw_features = self._query_features(_HAZ_WASTE_URL, "Hazardous Waste Generators")
        for feat in hw_features:
            try:
                facility = map_haz_waste(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping haz waste generator: %s", e)

        hw_count = len(seen_ids)
        logger.info("Unique hazardous waste generators: %s", hw_count)

        # Air Facility Locations
        air_features = self._query_features(_AIR_URL, "Air Facility Locations")
        for feat in air_features:
            try:
                facility = map_air_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air facility: %s", e)

        air_count = len(seen_ids) - hw_count
        logger.info("Unique air facilities: %s", air_count)

        # Public Drinking Water Systems
        water_features = self._query_features(_WATER_URL, "Public Drinking Water Systems")
        for feat in water_features:
            try:
                facility = map_drinking_water(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping drinking water system: %s", e)

        water_count = len(seen_ids) - hw_count - air_count
        logger.info("Unique drinking water systems: %s", water_count)

        # E-Start Cleanup Sites
        cleanup_features = self._query_features(_CLEANUP_URL, "E-Start Cleanup Sites")
        for feat in cleanup_features:
            try:
                facility = map_cleanup_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping cleanup site: %s", e)

        cleanup_count = len(seen_ids) - hw_count - air_count - water_count
        logger.info("Unique cleanup sites: %s", cleanup_count)

        # UST Facilities
        ust_features = self._query_features(_UST_URL, "UST Facilities")
        for feat in ust_features:
            try:
                facility = map_ust(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST facility: %s", e)

        ust_count = len(seen_ids) - hw_count - air_count - water_count - cleanup_count
        logger.info("Unique UST facilities: %s", ust_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """MO DNR does not publish structured violation data via ArcGIS.

        Violations for Missouri come from federal EPA ECHO (source='echo').
        """
        if state.upper() != "MO":
            logger.warning("Skipping %s (MO DNR is Missouri-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Missouri violations")
        return
        yield  # make this a generator

