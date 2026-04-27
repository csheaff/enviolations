"""Connector for IA DNR (Iowa Department of Natural Resources) data.

Downloads JSON data from Iowa DNR's ArcGIS REST services at programs.iowadnr.gov.
Four facility datasets from the OneStop/QueryEnvFacs MapServer:
  - Air Facilities (Layer 0): ~4,579 air-emitting facilities
  - Contaminated Sites (Layer 5): ~4,752 cleanup/remediation sites
  - Underground Storage Tanks (Layer 9): ~18,651 UST facilities
  - Wastewater NPDES (Layer 12): ~1,990 water discharge permits

IA DNR does not publish structured violation/enforcement data via ArcGIS;
violations for Iowa are covered by federal EPA ECHO.

This source only covers Iowa (state="IA").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ia_dnr_mapper import (
    map_air_facility,
    map_contaminated_site,
    map_npdes_facility,
    map_ust_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://programs.iowadnr.gov/geospatial/rest/services/OneStop/QueryEnvFacs/MapServer"

_AIR_URL = f"{_BASE}/0/query"
_CONTAMINATED_URL = f"{_BASE}/5/query"
_UST_URL = f"{_BASE}/9/query"
_NPDES_URL = f"{_BASE}/12/query"

class IADNRSource(ArcGISSource):
    """Connector for Iowa DNR ArcGIS REST services."""

    name = "ia_dnr"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "IA":
            logger.warning("Skipping %s (IA DNR is Iowa-only)", state)
            return

        seen_ids: set[str] = set()

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

        air_count = len(seen_ids)
        logger.info("Unique air facilities: %s", air_count)

        # Contaminated Sites
        contam_features = self._query_features(_CONTAMINATED_URL, "Contaminated Sites")
        for feat in contam_features:
            try:
                facility = map_contaminated_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping contaminated site: %s", e)

        contam_count = len(seen_ids) - air_count
        logger.info("Unique contaminated sites: %s", contam_count)

        # Underground Storage Tanks
        ust_features = self._query_features(_UST_URL, "Underground Storage Tanks")
        for feat in ust_features:
            try:
                facility = map_ust_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST facility: %s", e)

        ust_count = len(seen_ids) - air_count - contam_count
        logger.info("Unique UST facilities: %s", ust_count)

        # NPDES Wastewater
        npdes_features = self._query_features(_NPDES_URL, "Wastewater NPDES")
        for feat in npdes_features:
            try:
                facility = map_npdes_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping NPDES facility: %s", e)

        npdes_count = len(seen_ids) - air_count - contam_count - ust_count
        logger.info("Unique NPDES facilities: %s", npdes_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "IA":
            logger.warning("Skipping %s (IA DNR is Iowa-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Iowa violations")
        return
        yield  # make this a generator

