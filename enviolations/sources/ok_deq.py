"""Connector for OK DEQ (Oklahoma Department of Environmental Quality) data.

Downloads JSON data from OK DEQ's ArcGIS REST services at gis.deq.ok.gov.
Three facility datasets:
  - AirWeb Point Source Emissions (MapServer/8): ~15,196 air-emitting facilities
  - LandWeb Tier II Facilities (MapServer/9): ~6,994 hazardous chemical reporters
  - WaterWeb NPDES Dischargers (MapServer/9): ~1,498 water discharge permits

OK DEQ does not publish structured violation/enforcement data via ArcGIS;
violations for Oklahoma are covered by federal EPA ECHO.

This source only covers Oklahoma (state="OK").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ok_deq_mapper import map_air_facility, map_npdes_facility, map_tier2_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://gis.deq.ok.gov/server/rest/services"

_AIR_URL = f"{_BASE}/AirWeb/MapServer/8/query"
_TIER2_URL = f"{_BASE}/LandWeb/MapServer/9/query"
_NPDES_URL = f"{_BASE}/WaterWeb/MapServer/9/query"

class OKDEQSource(ArcGISSource):
    """Connector for Oklahoma DEQ ArcGIS REST services."""

    name = "ok_deq"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "OK":
            logger.warning("Skipping %s (OK DEQ is Oklahoma-only)", state)
            return

        seen_ids: set[str] = set()

        # Air Point Source Emissions
        air_features = self._query_features(_AIR_URL, "Air Point Source Emissions")
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

        # Tier II Facilities
        tier2_features = self._query_features(_TIER2_URL, "Tier II Facilities")
        for feat in tier2_features:
            try:
                facility = map_tier2_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping Tier II facility: %s", e)

        tier2_count = len(seen_ids) - air_count
        logger.info("Unique Tier II facilities: %s", tier2_count)

        # NPDES Dischargers
        npdes_features = self._query_features(_NPDES_URL, "NPDES Dischargers")
        for feat in npdes_features:
            try:
                facility = map_npdes_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping NPDES facility: %s", e)

        npdes_count = len(seen_ids) - air_count - tier2_count
        logger.info("Unique NPDES facilities: %s", npdes_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "OK":
            logger.warning("Skipping %s (OK DEQ is Oklahoma-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Oklahoma violations")
        return
        yield  # make this a generator

