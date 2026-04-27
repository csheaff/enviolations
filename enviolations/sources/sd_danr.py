"""Connector for SD DANR (South Dakota Dept of Agriculture & Natural Resources) data.

Downloads JSON data from SD DANR's ArcGIS REST services at arcgis.sd.gov.
Three facility datasets:
  - Tank Facilities (NR40_TankFacilities_Public/MapServer/0): ~4,874 UST/AST sites
  - Air Quality (NR34_AirQuality_View/MapServer/0): ~2,133 air-permitted facilities
  - Solid Waste (NR60_SolidWaste/MapServer/0): ~1,002 landfill/waste facilities

SD DANR does not publish structured violation/enforcement data via ArcGIS;
violations for South Dakota are covered by federal EPA ECHO.

This source only covers South Dakota (state="SD").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.sd_danr_mapper import map_air_facility, map_solid_waste, map_tank
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://arcgis.sd.gov/arcgis/rest/services/DENR"

_TANK_URL = f"{_BASE}/NR40_TankFacilities_Public/MapServer/0/query"
_AIR_URL = f"{_BASE}/NR34_AirQuality_View/MapServer/0/query"
_SOLIDWASTE_URL = f"{_BASE}/NR60_SolidWaste/MapServer/0/query"

class SDDANRSource(ArcGISSource):
    """Connector for South Dakota DANR ArcGIS REST services."""

    name = "sd_danr"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "SD":
            logger.warning("Skipping %s (SD DANR is South Dakota-only)", state)
            return

        seen_ids: set[str] = set()

        # Tank Facilities
        tank_features = self._query_features(_TANK_URL, "Tank Facilities")
        for feat in tank_features:
            try:
                facility = map_tank(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping tank: %s", e)

        tank_count = len(seen_ids)
        logger.info("Unique tank facilities: %s", tank_count)

        # Air Quality Facilities
        air_features = self._query_features(_AIR_URL, "Air Quality Facilities")
        for feat in air_features:
            try:
                facility = map_air_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air facility: %s", e)

        air_count = len(seen_ids) - tank_count
        logger.info("Unique air facilities: %s", air_count)

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

        sw_count = len(seen_ids) - tank_count - air_count
        logger.info("Unique solid waste facilities: %s", sw_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "SD":
            logger.warning("Skipping %s (SD DANR is South Dakota-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for South Dakota violations")
        return
        yield  # make this a generator

