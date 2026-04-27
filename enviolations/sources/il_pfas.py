"""Connector for IL EPA PFAS Sampling data.

Downloads JSON data from IL EPA's ArcGIS REST services at
geoservices.epa.illinois.gov. Single layer from
Water/PfasSamplingResults/MapServer/0:
  - ~1,428 public water system PFAS sampling results
  - Includes per-compound concentrations (PFOA, PFOS, PFBS, PFHxA, etc.)

Separate from il_epa because PFAS sampling data uses a different ArcGIS
service and data structure than the federal facilities/landfills/LUST/OER
datasets.

This source only covers Illinois (state="IL").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.il_pfas_mapper import map_pfas_system
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_PFAS_SAMPLING_URL = (
    "https://geoservices.epa.illinois.gov/arcgis/rest/services"
    "/Water/PfasSamplingResults/MapServer/0/query"
)


class ILPFASSource(ArcGISSource):
    """Connector for IL EPA PFAS Sampling ArcGIS REST services."""

    name = "il_pfas"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "IL":
            logger.warning("Skipping %s (IL PFAS is Illinois-only)", state)
            return

        seen_ids: set[str] = set()

        features = self._query_features(_PFAS_SAMPLING_URL, "PFAS Sampling Results")
        for feat in features:
            try:
                facility = map_pfas_system(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping PFAS system: %s", e)

        logger.info("Unique PFAS systems: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "IL":
            return
        return
        yield  # noqa: unreachable — makes this a generator
