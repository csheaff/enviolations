"""Connector for ND DEQ (North Dakota Department of Environmental Quality) data.

Downloads JSON data from ND GIS Hub ArcGIS at ndgishub.nd.gov.
Two facility datasets from All_Locations MapServer:
  - Landfills (Layer 12): ~346 solid waste facilities
  - Abandoned Mines (Layer 6): ~1,747 historical mine sites

ND DEQ does not publish structured violation/enforcement data via ArcGIS;
violations for North Dakota are covered by federal EPA ECHO.

This source only covers North Dakota (state="ND").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.nd_deq_mapper import map_abandoned_mine, map_landfill
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://ndgishub.nd.gov/ArcGIS/rest/services/All_Locations/MapServer"

_LANDFILL_URL = f"{_BASE}/12/query"
_MINE_URL = f"{_BASE}/6/query"

class NDDEQSource(ArcGISSource):
    """Connector for North Dakota DEQ ArcGIS REST services."""

    name = "nd_deq"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "ND":
            logger.warning("Skipping %s (ND DEQ is North Dakota-only)", state)
            return

        seen_ids: set[str] = set()

        # Landfills
        lf_features = self._query_features(_LANDFILL_URL, "Landfills")
        for feat in lf_features:
            try:
                facility = map_landfill(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping landfill: %s", e)

        lf_count = len(seen_ids)
        logger.info("Unique landfills: %s", lf_count)

        # Abandoned Mines
        mine_features = self._query_features(_MINE_URL, "Abandoned Mines")
        for feat in mine_features:
            try:
                facility = map_abandoned_mine(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping mine: %s", e)

        mine_count = len(seen_ids) - lf_count
        logger.info("Unique mines: %s", mine_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "ND":
            logger.warning("Skipping %s (ND DEQ is North Dakota-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for North Dakota violations")
        return
        yield  # make this a generator

