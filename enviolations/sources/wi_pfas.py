"""Connector for WI DNR PFAS data.

Downloads JSON data from WI DNR's ArcGIS REST services at dnrmaps.wi.gov.
Two layers from EM_PFAS/EM_PFAS_MAPLAYERS_PUBLIC_EXT/MapServer:
  - Layer 1: Open PFAS sites (80 active cleanup/investigation sites)
  - Layer 2: Closed PFAS sites (2 completed cleanup sites)

Separate from wi_dnr because PFAS cleanup sites use a different ArcGIS
service and data structure than the air/remediation/water datasets.

This source only covers Wisconsin (state="WI").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.wi_pfas_mapper import map_pfas_site
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_OPEN_SITES_URL = (
    "https://dnrmaps.wi.gov/arcgis2/rest/services"
    "/EM_PFAS/EM_PFAS_MAPLAYERS_PUBLIC_EXT/MapServer/1/query"
)
_CLOSED_SITES_URL = (
    "https://dnrmaps.wi.gov/arcgis2/rest/services"
    "/EM_PFAS/EM_PFAS_MAPLAYERS_PUBLIC_EXT/MapServer/2/query"
)


class WIPFASSource(ArcGISSource):
    """Connector for WI DNR PFAS ArcGIS REST services."""

    name = "wi_pfas"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "WI":
            logger.warning("Skipping %s (WI PFAS is Wisconsin-only)", state)
            return

        seen_ids: set[str] = set()

        # Open PFAS sites
        open_features = self._query_features(_OPEN_SITES_URL, "PFAS Open Sites")
        for feat in open_features:
            try:
                facility = map_pfas_site(feat, status="Open")
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping open PFAS site: %s", e)

        open_count = len(seen_ids)
        logger.info("Open PFAS sites: %s", open_count)

        # Closed PFAS sites
        closed_features = self._query_features(_CLOSED_SITES_URL, "PFAS Closed Sites")
        for feat in closed_features:
            try:
                facility = map_pfas_site(feat, status="Closed")
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping closed PFAS site: %s", e)

        closed_count = len(seen_ids) - open_count
        logger.info("Closed PFAS sites: %s", closed_count)
        logger.info("Total unique PFAS sites: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "WI":
            return
        return
        yield  # noqa: unreachable — makes this a generator
