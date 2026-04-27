"""Connector for NJ DEP PFAS data.

Downloads JSON data from NJ DEP's ArcGIS REST services. Two datasets:
  - PFAS Source Evaluation Survey (MapServer/125): ~161 facilities surveyed
    for PFAS sources under NJPDES permits
  - PFAS Sampling Composite (FeatureServer/141): ~11,228 sampling results
    across soil, groundwater, surface water, and drinking water

NJ has the strictest PFAS standards in the nation (14 ppt PFOA, 13 ppt PFOS).
Separate from nj_dep because PFAS data uses different endpoints and data
structures than the NJEMS environmental management system.

This source only covers New Jersey (state="NJ").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.nj_pfas_mapper import map_sampling_site, map_source_survey_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_SOURCE_SURVEY_URL = (
    "https://mapsdep.nj.gov/arcgis/rest/services"
    "/Features/Environmental/MapServer/125/query"
)
_SAMPLING_COMPOSITE_URL = (
    "https://services1.arcgis.com/QWdNfRs7lkPq4g4Q/arcgis/rest/services"
    "/NJDEP_PFAS_Sampling_Composite/FeatureServer/141/query"
)


class NJPFASSource(ArcGISSource):
    """Connector for NJ DEP PFAS ArcGIS REST services."""

    name = "nj_pfas"
    page_size = 2000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "NJ":
            logger.warning("Skipping %s (NJ PFAS is New Jersey-only)", state)
            return

        seen_ids: set[str] = set()

        # PFAS Source Evaluation Survey
        survey_features = self._query_features(
            _SOURCE_SURVEY_URL, "PFAS Source Survey"
        )
        for feat in survey_features:
            try:
                facility = map_source_survey_facility(
                    feat["attributes"], feat.get("geometry")
                )
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping PFAS survey facility: %s", e)

        survey_count = len(seen_ids)
        logger.info("Unique PFAS survey facilities: %s", survey_count)

        # PFAS Sampling Composite
        sampling_features = self._query_features(
            _SAMPLING_COMPOSITE_URL, "PFAS Sampling Composite"
        )
        for feat in sampling_features:
            try:
                facility = map_sampling_site(
                    feat["attributes"], feat.get("geometry")
                )
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping PFAS sampling site: %s", e)

        sampling_count = len(seen_ids) - survey_count
        logger.info("Unique PFAS sampling sites: %s", sampling_count)
        logger.info("Total unique PFAS facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "NJ":
            return
        return
        yield  # noqa: unreachable — makes this a generator
