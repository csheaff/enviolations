"""Connector for Ohio EPA PFAS Sampling data.

Downloads JSON data from Ohio EPA's ArcGIS REST services at
geo.epa.ohio.gov. Single layer from DrinkingWater/PFAS_SAMPLING/MapServer/0:
  - ~1,569 water treatment plants sampled for PFAS compounds
  - Includes detection status, system type, source type, and population served

Separate from oh_epa because PFAS sampling data uses a different ArcGIS
service and data structure than the NPDES/DMWM/DERR/Spills datasets.

This source only covers Ohio (state="OH").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.oh_pfas_mapper import map_pfas_plant
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_PFAS_SAMPLING_URL = (
    "https://geo.epa.ohio.gov/arcgis/rest/services"
    "/DrinkingWater/PFAS_SAMPLING/MapServer/0/query"
)


class OHPFASSource(ArcGISSource):
    """Connector for Ohio EPA PFAS Sampling ArcGIS REST services."""

    name = "oh_pfas"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "OH":
            logger.warning("Skipping %s (OH PFAS is Ohio-only)", state)
            return

        seen_ids: set[str] = set()

        features = self._query_features(_PFAS_SAMPLING_URL, "PFAS Sampling")
        for feat in features:
            try:
                facility = map_pfas_plant(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping PFAS plant: %s", e)

        logger.info("Unique PFAS plants: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "OH":
            return
        return
        yield  # noqa: unreachable — makes this a generator
