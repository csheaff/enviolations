"""Connector for DE DNREC (Delaware Dept of Natural Resources & Environmental Control).

Downloads facility data from Delaware's FirstMap ArcGIS Enterprise server.
Four facility datasets:
  - UST Facilities (DE_DNREC_Permits/FeatureServer/0): ~5,978 underground storage tanks
  - Leaking UST (DE_DNREC_Permits/FeatureServer/1): ~4,501 leaking UST sites
  - Air Permitted Facilities (DE_DNREC_Facilities/FeatureServer/1): ~1,505 air sources
  - Hazardous Waste Generators (DE_DNREC_Facilities/FeatureServer/2): ~625 generators

DE DNREC does not publish structured violation/enforcement data via ArcGIS;
violations for Delaware are covered by federal EPA ECHO.

Note: Default coordinates are in Delaware State Plane (WKID 26957).
We request outSR=4326 for WGS84 lat/lon.

This source only covers Delaware (state="DE").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.de_dnrec_mapper import (
    map_air_facility,
    map_hazwaste_generator,
    map_leaking_ust,
    map_ust_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE_PERMITS = "https://enterprise.firstmap.delaware.gov/arcgis/rest/services/Environmental/DE_DNREC_Permits/FeatureServer"
_BASE_FACILITIES = "https://enterprise.firstmap.delaware.gov/arcgis/rest/services/Environmental/DE_DNREC_Facilities/FeatureServer"

_UST_URL = f"{_BASE_PERMITS}/0/query"
_LUST_URL = f"{_BASE_PERMITS}/1/query"
_AIR_URL = f"{_BASE_FACILITIES}/1/query"
_HAZWASTE_URL = f"{_BASE_FACILITIES}/2/query"

class DEDNRECSource(ArcGISSource):
    """Connector for Delaware DNREC ArcGIS FeatureServer."""

    name = "de_dnrec"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "DE":
            logger.warning("Skipping %s (DE DNREC is Delaware-only)", state)
            return

        seen_ids: set[str] = set()

        # UST Facilities
        ust_features = self._query_features(_UST_URL, "UST Facilities")
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

        # Leaking UST
        lust_features = self._query_features(_LUST_URL, "Leaking UST")
        for feat in lust_features:
            try:
                facility = map_leaking_ust(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping LUST site: %s", e)

        lust_count = len(seen_ids) - ust_count
        logger.info("Unique leaking UST sites: %s", lust_count)

        # Air Permitted Facilities
        air_features = self._query_features(_AIR_URL, "Air Permitted Facilities")
        for feat in air_features:
            try:
                facility = map_air_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air facility: %s", e)

        air_count = len(seen_ids) - ust_count - lust_count
        logger.info("Unique air facilities: %s", air_count)

        # Hazardous Waste Generators
        hw_features = self._query_features(_HAZWASTE_URL, "Hazardous Waste Generators")
        for feat in hw_features:
            try:
                facility = map_hazwaste_generator(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping hazwaste generator: %s", e)

        hw_count = len(seen_ids) - ust_count - lust_count - air_count
        logger.info("Unique hazwaste generators: %s", hw_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "DE":
            logger.warning("Skipping %s (DE DNREC is Delaware-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Delaware violations")
        return
        yield  # make this a generator

