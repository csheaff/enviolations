"""Connector for NV DEP (Nevada Division of Environmental Protection) data.

Downloads JSON data from NV DEP's ArcGIS REST services at ndep-emap.ndep.nv.gov.
Three facility datasets:
  - BCA Sites (eMap_BCA/MapServer/0+1): ~3,790 corrective action sites (open + closed)
  - Air Facilities (eMap_Air/MapServer/1): ~1,547 air-regulated facilities
  - Water Permits (eMap_BWPC/MapServer/1): ~593 water pollution control permits

NV DEP does not publish structured violation/enforcement data via ArcGIS;
violations for Nevada are covered by federal EPA ECHO.

This source only covers Nevada (state="NV").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.nv_dep_mapper import map_air_facility, map_bca_site, map_water_permit
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://ndep-emap.ndep.nv.gov/arcgis/rest/services/eMap_Services"

_BCA_OPEN_URL = f"{_BASE}/eMap_BCA/MapServer/0/query"
_BCA_CLOSED_URL = f"{_BASE}/eMap_BCA/MapServer/1/query"
_AIR_URL = f"{_BASE}/eMap_Air/MapServer/1/query"
_WATER_URL = f"{_BASE}/eMap_BWPC/MapServer/1/query"

class NVDEPSource(ArcGISSource):
    """Connector for Nevada DEP ArcGIS REST services."""

    name = "nv_dep"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "NV":
            logger.warning("Skipping %s (NV DEP is Nevada-only)", state)
            return

        seen_ids: set[str] = set()

        # BCA Open Sites
        bca_open = self._query_features(_BCA_OPEN_URL, "BCA Open Sites")
        for feat in bca_open:
            try:
                facility = map_bca_site(feat, status="Open")
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping BCA open site: %s", e)

        # BCA Closed Sites
        bca_closed = self._query_features(_BCA_CLOSED_URL, "BCA Closed Sites")
        for feat in bca_closed:
            try:
                facility = map_bca_site(feat, status="Closed")
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping BCA closed site: %s", e)

        bca_count = len(seen_ids)
        logger.info("Unique BCA sites: %s", bca_count)

        # Air Facilities
        air_features = self._query_features(_AIR_URL, "Air Facilities")
        for feat in air_features:
            try:
                facility = map_air_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air facility: %s", e)

        air_count = len(seen_ids) - bca_count
        logger.info("Unique air facilities: %s", air_count)

        # Water Permits
        water_features = self._query_features(_WATER_URL, "Water Permits")
        for feat in water_features:
            try:
                facility = map_water_permit(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping water permit: %s", e)

        water_count = len(seen_ids) - bca_count - air_count
        logger.info("Unique water permits: %s", water_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "NV":
            logger.warning("Skipping %s (NV DEP is Nevada-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Nevada violations")
        return
        yield  # make this a generator

