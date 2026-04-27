"""Connector for WI DNR (Wisconsin Department of Natural Resources) data.

Downloads JSON data from WI DNR's ArcGIS REST services at dnrmaps.wi.gov.
Three facility datasets:
  - Air Management (AM_WARP): 5,168 air-permitted facilities
  - BRRTS Remediation (RR_Sites_Map): 11,669 remediation activity sites
  - WPDES Water Permits (WT_SWDV): 1,779 water discharge permits

WI DNR does not publish structured violation/enforcement data via ArcGIS;
violations for Wisconsin are covered by federal EPA ECHO.

This source only covers Wisconsin (state="WI").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.wi_dnr_mapper import map_air_facility, map_brrts_site, map_wpdes_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API URLs
_AIR_URL = "https://dnrmaps.wi.gov/arcgis/rest/services/AM_WARP_MAP/AM_WARP_WTM_Int/MapServer/0/query"
_BRRTS_URL = "https://dnrmaps.wi.gov/arcgis/rest/services/RR_Sites_Map/RR_PUBLIC_MAPSERVICES_CORE_EXT/MapServer/101/query"
_WPDES_URL = "https://dnrmaps.wi.gov/arcgis/rest/services/WT_SWDV/WY_SURFACE_WATER_PERMITS/MapServer/8/query"

class WIDNRSource(ArcGISSource):
    """Connector for Wisconsin DNR ArcGIS REST services."""

    name = "wi_dnr"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "WI":
            logger.warning("Skipping %s (WI DNR is Wisconsin-only)", state)
            return

        seen_ids: set[str] = set()

        # Air Management facilities
        air_features = self._query_features(_AIR_URL, "Air Management", page_size=1000)
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

        # BRRTS Remediation sites
        brrts_features = self._query_features(_BRRTS_URL, "BRRTS Remediation")
        for feat in brrts_features:
            try:
                facility = map_brrts_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping BRRTS site: %s", e)

        brrts_count = len(seen_ids) - air_count
        logger.info("Unique BRRTS sites: %s", brrts_count)

        # WPDES Water Permits
        wpdes_features = self._query_features(_WPDES_URL, "WPDES Water Permits")
        for feat in wpdes_features:
            try:
                facility = map_wpdes_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping WPDES facility: %s", e)

        wpdes_count = len(seen_ids) - air_count - brrts_count
        logger.info("Unique WPDES facilities: %s", wpdes_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "WI":
            logger.warning("Skipping %s (WI DNR is Wisconsin-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Wisconsin violations")
        return
        yield  # make this a generator

