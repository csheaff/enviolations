"""Connector for KY DEP (Kentucky Department for Environmental Protection) data.

Downloads JSON data from Kentucky's ArcGIS REST services at watermaps.ky.gov.
Three facility datasets:
  - Underground Storage Tanks (MapServer/0): ~57,419 UST records
  - Superfund Sites (MapServer/0): ~6,642 cleanup sites
  - Brownfields (MapServer/0): ~285 brownfield sites

KY DEP does not publish structured violation/enforcement data via ArcGIS;
violations for Kentucky are covered by federal EPA ECHO.

Note: Kentucky's GIS servers have SSL certificate issues, so verify=False is required.

This source only covers Kentucky (state="KY").
"""

from __future__ import annotations

from typing import Iterator
import logging

import httpx

from ..models import Facility, Violation
from ..normalize.ky_dep_mapper import map_brownfield, map_superfund_site, map_ust_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://www.watermaps.ky.gov/arcgis/rest/services/WebMapServices"

_UST_URL = f"{_BASE}/Underground_Storage_Tanks/MapServer/0/query"
_SUPERFUND_URL = f"{_BASE}/Superfund_Sites/MapServer/0/query"
_BROWNFIELD_URL = f"{_BASE}/Brownfields_Superfunds/MapServer/0/query"

class KYDEPSource(ArcGISSource):
    """Connector for Kentucky DEP ArcGIS REST services."""

    name = "ky_dep"

    def __init__(self) -> None:
        super().__init__()
        # Kentucky GIS servers have SSL certificate issues
        self._client = httpx.Client(timeout=self.timeout, verify=False)

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "KY":
            logger.warning("Skipping %s (KY DEP is Kentucky-only)", state)
            return

        seen_ids: set[str] = set()

        # Underground Storage Tanks — dedup by AI_ID since data is per-tank
        ust_features = self._query_features(_UST_URL, "Underground Storage Tanks")
        seen_ai_ids: set[str] = set()
        for feat in ust_features:
            try:
                attrs = feat.get("attributes", {})
                ai_id = str(attrs.get("AI_ID", "")).strip()
                if ai_id and ai_id in seen_ai_ids:
                    continue
                if ai_id:
                    seen_ai_ids.add(ai_id)
                facility = map_ust_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST facility: %s", e)

        ust_count = len(seen_ids)
        logger.info("Unique UST facilities: %s", ust_count)

        # Superfund Sites
        sf_features = self._query_features(_SUPERFUND_URL, "Superfund Sites")
        for feat in sf_features:
            try:
                facility = map_superfund_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping superfund site: %s", e)

        sf_count = len(seen_ids) - ust_count
        logger.info("Unique superfund sites: %s", sf_count)

        # Brownfields
        bf_features = self._query_features(_BROWNFIELD_URL, "Brownfields")
        for feat in bf_features:
            try:
                facility = map_brownfield(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping brownfield: %s", e)

        bf_count = len(seen_ids) - ust_count - sf_count
        logger.info("Unique brownfields: %s", bf_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "KY":
            logger.warning("Skipping %s (KY DEP is Kentucky-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Kentucky violations")
        return
        yield  # make this a generator

