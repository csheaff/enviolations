"""Connector for MN PCA (Minnesota Pollution Control Agency) data.

Downloads JSON data from MPCA's ArcGIS REST services at
pca-gis02.pca.state.mn.us. Primary dataset:
  - WIMN/sites (Layer 1) → facilities (~189K MPCA-regulated sites)

MPCA does not publish a structured violation/enforcement dataset via ArcGIS;
violations for Minnesota are covered by federal EPA ECHO.

This source only covers Minnesota (state="MN").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.mn_pca_mapper import map_wimn_site
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API base
_ARCGIS_BASE = (
    "https://pca-gis02.pca.state.mn.us/arcgis/rest/services"
)
_WIMN_SITES_URL = f"{_ARCGIS_BASE}/WIMN/sites/MapServer/1/query"

class MNPCASource(ArcGISSource):
    """Connector for Minnesota PCA ArcGIS REST services."""

    name = "mn_pca"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from MN PCA WIMN sites.

        MN PCA only covers Minnesota. Returns empty for non-MN states.
        """
        if state.upper() != "MN":
            logger.warning("Skipping %s (MN PCA is Minnesota-only)", state)
            return

        seen_ids: set[str] = set()

        # WIMN Sites (main regulated sites layer)
        features = self._query_features(_WIMN_SITES_URL, "WIMN Sites")
        for feat in features:
            try:
                facility = map_wimn_site(feat.get("attributes", feat))
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping WIMN site: %s", e)

        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """MN PCA does not publish structured violation data via ArcGIS.

        Violations for Minnesota come from federal EPA ECHO (source='echo').
        """
        if state.upper() != "MN":
            logger.warning("Skipping %s (MN PCA is Minnesota-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Minnesota violations")
        return
        yield  # make this a generator

