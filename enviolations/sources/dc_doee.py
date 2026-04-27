"""Connector for DC DOEE (District of Columbia Dept of Energy & Environment) data.

Downloads JSON data from DC GIS at maps2.dcgis.dc.gov. Three facility datasets
from Facility_and_Structure_WebMercator MapServer:
  - Underground Storage Tanks (Layer 11): ~800 UST facilities
  - Leaking Underground Storage Tanks (Layer 16): ~500 LUST cases
  - Above Ground Storage Tanks (Layer 10): ~200 AST facilities

DC DOEE does not publish structured violation/enforcement data via ArcGIS;
violations for DC are covered by federal EPA ECHO.

This source only covers District of Columbia (state="DC").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.dc_doee_mapper import map_ast, map_lust, map_ust
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = (
    "https://maps2.dcgis.dc.gov/dcgis/rest/services"
    "/DCGIS_DATA/Facility_and_Structure_WebMercator/MapServer"
)
_AST_URL = f"{_BASE}/10/query"
_UST_URL = f"{_BASE}/11/query"
_LUST_URL = f"{_BASE}/16/query"

class DCDOEESource(ArcGISSource):
    """Connector for DC DOEE ArcGIS REST services."""

    name = "dc_doee"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "DC":
            logger.warning("Skipping %s (DC DOEE is DC-only)", state)
            return

        seen_ids: set[str] = set()

        # Underground Storage Tanks
        ust_features = self._query_features(_UST_URL, "Underground Storage Tanks")
        for feat in ust_features:
            try:
                facility = map_ust(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST: %s", e)

        ust_count = len(seen_ids)
        logger.info("Unique UST facilities: %s", ust_count)

        # Leaking Underground Storage Tanks
        lust_features = self._query_features(_LUST_URL, "Leaking Underground Storage Tanks")
        for feat in lust_features:
            try:
                facility = map_lust(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping LUST: %s", e)

        lust_count = len(seen_ids) - ust_count
        logger.info("Unique LUST facilities: %s", lust_count)

        # Above Ground Storage Tanks
        ast_features = self._query_features(_AST_URL, "Above Ground Storage Tanks")
        for feat in ast_features:
            try:
                facility = map_ast(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping AST: %s", e)

        ast_count = len(seen_ids) - ust_count - lust_count
        logger.info("Unique AST facilities: %s", ast_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "DC":
            logger.warning("Skipping %s (DC DOEE is DC-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for DC violations")
        return
        yield  # make this a generator

