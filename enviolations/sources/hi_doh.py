"""Connector for HI DOH (Hawaii Department of Health) data.

Downloads JSON data from Hawaii Statewide GIS at geodata.hawaii.gov.
Two facility datasets:
  - Brightfields Initiative Data (LandUseLandCover MapServer/13): brownfield
    and contaminated sites with DOH identifiers
  - Regulated Dams (Infrastructure MapServer/10): ~124 state-regulated dams

HI DOH does not publish structured violation/enforcement data via ArcGIS;
violations for Hawaii are covered by federal EPA ECHO.

This source only covers Hawaii (state="HI").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.hi_doh_mapper import map_brightfield, map_dam
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BRIGHTFIELD_URL = (
    "https://geodata.hawaii.gov/arcgis/rest/services"
    "/LandUseLandCover/MapServer/13/query"
)
_DAM_URL = (
    "https://geodata.hawaii.gov/arcgis/rest/services"
    "/Infrastructure/MapServer/10/query"
)

class HIDOHSource(ArcGISSource):
    """Connector for Hawaii DOH ArcGIS REST services."""

    name = "hi_doh"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "HI":
            logger.warning("Skipping %s (HI DOH is Hawaii-only)", state)
            return

        seen_ids: set[str] = set()

        # Brightfields Initiative Data (brownfield/contaminated sites)
        bf_features = self._query_features(_BRIGHTFIELD_URL, "Brightfields Initiative")
        for feat in bf_features:
            try:
                facility = map_brightfield(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping brightfield site: %s", e)

        bf_count = len(seen_ids)
        logger.info("Unique brightfield sites: %s", bf_count)

        # Regulated Dams
        dam_features = self._query_features(_DAM_URL, "Regulated Dams")
        for feat in dam_features:
            try:
                facility = map_dam(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping dam: %s", e)

        dam_count = len(seen_ids) - bf_count
        logger.info("Unique dams: %s", dam_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "HI":
            logger.warning("Skipping %s (HI DOH is Hawaii-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Hawaii violations")
        return
        yield  # make this a generator

