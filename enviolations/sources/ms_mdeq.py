"""Connector for MS MDEQ (Mississippi Dept of Environmental Quality) data.

Downloads facility data from MDEQ's ArcGIS REST services at
opcgis.deq.state.ms.us.

Two facility datasets:
  - All Underground Storage Tanks (MUSTER/MapServer/10): ~thousands of UST facilities
  - GARD CERCLA Sites (MUSTER/MapServer/12): Superfund/uncontrolled sites

MS MDEQ does not publish structured violation/enforcement data via ArcGIS;
violations for Mississippi are covered by federal EPA ECHO.

This source only covers Mississippi (state="MS").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ms_mdeq_mapper import map_cercla_site, map_ust_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://opcgis.deq.state.ms.us/opcgis/rest/services/Compendium/MUSTER/MapServer"

_UST_URL = f"{_BASE}/10/query"
_CERCLA_URL = f"{_BASE}/12/query"

class MSMDEQSource(ArcGISSource):
    """Connector for Mississippi MDEQ MUSTER ArcGIS MapServer."""

    name = "ms_mdeq"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "MS":
            logger.warning("Skipping %s (MS MDEQ is Mississippi-only)", state)
            return

        seen_ids: set[str] = set()

        # Underground Storage Tanks
        ust_features = self._query_features(_UST_URL, "All UST Facilities")
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

        # CERCLA Sites
        cercla_features = self._query_features(_CERCLA_URL, "GARD CERCLA Sites")
        for feat in cercla_features:
            try:
                facility = map_cercla_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping CERCLA site: %s", e)

        cercla_count = len(seen_ids) - ust_count
        logger.info("Unique CERCLA sites: %s", cercla_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "MS":
            logger.warning("Skipping %s (MS MDEQ is Mississippi-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Mississippi violations")
        return
        yield  # make this a generator

