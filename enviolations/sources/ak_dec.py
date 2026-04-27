"""Connector for AK DEC (Alaska Department of Environmental Conservation) data.

Downloads JSON data from Alaska DEC ArcGIS REST services at dec.alaska.gov.
Three facility datasets:
  - Contaminated Sites (SPAR FeatureServer/1): ~8,782 contaminated sites incl. LUST
  - Solid Waste Sites (EH FeatureServer/3): ~745 landfills and solid waste facilities
  - Active Regulated UST Facilities (SPAR FeatureServer/0): ~398 underground storage tanks

AK DEC does not publish structured violation/enforcement data via ArcGIS;
violations for Alaska are covered by federal EPA ECHO.

This source only covers Alaska (state="AK").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ak_dec_mapper import map_contaminated_site, map_solid_waste, map_ust
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_CONTAM_URL = (
    "https://dec.alaska.gov/arcgis/rest/services"
    "/SPAR/Contaminated_Sites/FeatureServer/1/query"
)
_SOLID_WASTE_URL = (
    "https://dec.alaska.gov/arcgis/rest/services"
    "/EH/Solid_Waste_Sites/FeatureServer/3/query"
)
_UST_URL = (
    "https://dec.alaska.gov/arcgis/rest/services"
    "/SPAR/Alaska_DEC_Active_Regulated_Underground_Storage_Tank_Facilities"
    "/FeatureServer/0/query"
)

class AKDECSource(ArcGISSource):
    """Connector for Alaska DEC ArcGIS REST services."""

    name = "ak_dec"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "AK":
            logger.warning("Skipping %s (AK DEC is Alaska-only)", state)
            return

        seen_ids: set[str] = set()

        # Contaminated Sites (includes LUST)
        contam_features = self._query_features(_CONTAM_URL, "Contaminated Sites")
        for feat in contam_features:
            try:
                facility = map_contaminated_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping contaminated site: %s", e)

        contam_count = len(seen_ids)
        logger.info("Unique contaminated sites: %s", contam_count)

        # Solid Waste Sites
        sw_features = self._query_features(_SOLID_WASTE_URL, "Solid Waste Sites")
        for feat in sw_features:
            try:
                facility = map_solid_waste(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping solid waste site: %s", e)

        sw_count = len(seen_ids) - contam_count
        logger.info("Unique solid waste sites: %s", sw_count)

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

        ust_count = len(seen_ids) - contam_count - sw_count
        logger.info("Unique UST facilities: %s", ust_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "AK":
            logger.warning("Skipping %s (AK DEC is Alaska-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Alaska violations")
        return
        yield  # make this a generator

