"""Connector for MT DEQ (Montana Department of Environmental Quality) data.

Downloads JSON data from MT DEQ's ArcGIS Hosted FeatureServer at gis.mtdeq.us.
Three publicly accessible facility datasets:
  - UST Facilities (Montana_UST_Facilities_/FeatureServer/0): ~1,131 underground storage tanks
  - Solid Waste Facilities (Montana_Solid_Waste_Facilities/FeatureServer/0): ~390 waste facilities
  - Opencut Mining Sites (Montana_Opencut_Mining_Sites/FeatureServer/0): ~1,618 mining sites

Note: Some MT DEQ services (Hazardous Waste Handlers, State Superfund) require
authentication tokens and are not included in this connector.

MT DEQ does not publish structured violation/enforcement data via ArcGIS;
violations for Montana are covered by federal EPA ECHO.

This source only covers Montana (state="MT").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.mt_deq_mapper import map_opencut_mine, map_solid_waste, map_ust
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://gis.mtdeq.us/hosting/rest/services/Hosted"

_UST_URL = f"{_BASE}/Montana_UST_Facilities_/FeatureServer/0/query"
_SOLIDWASTE_URL = f"{_BASE}/Montana_Solid_Waste_Facilities/FeatureServer/0/query"
_MINE_URL = f"{_BASE}/Montana_Opencut_Mining_Sites/FeatureServer/0/query"

class MTDEQSource(ArcGISSource):
    """Connector for Montana DEQ ArcGIS Hosted FeatureServer."""

    name = "mt_deq"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "MT":
            logger.warning("Skipping %s (MT DEQ is Montana-only)", state)
            return

        seen_ids: set[str] = set()

        # UST Facilities
        ust_features = self._query_features(_UST_URL, "UST Facilities")
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

        # Solid Waste Facilities
        sw_features = self._query_features(_SOLIDWASTE_URL, "Solid Waste Facilities")
        for feat in sw_features:
            try:
                facility = map_solid_waste(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping solid waste: %s", e)

        sw_count = len(seen_ids) - ust_count
        logger.info("Unique solid waste facilities: %s", sw_count)

        # Opencut Mining Sites
        mine_features = self._query_features(_MINE_URL, "Opencut Mining Sites")
        for feat in mine_features:
            try:
                facility = map_opencut_mine(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping mine: %s", e)

        mine_count = len(seen_ids) - ust_count - sw_count
        logger.info("Unique mining sites: %s", mine_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "MT":
            logger.warning("Skipping %s (MT DEQ is Montana-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Montana violations")
        return
        yield  # make this a generator

