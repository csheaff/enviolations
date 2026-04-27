"""Connector for WA ECY (Washington Dept. of Ecology).

Downloads JSON data from WA ECY's ArcGIS REST FeatureServer. One dataset:
  - ECY/FeatureServer/1: FacilitySiteInteractions → facilities (deduplicated by FSID)
                                                   → violations (InteractionType='ENFORFNL')

The interactions layer contains one row per facility-program interaction
(permits, inspections, etc.). We deduplicate by FSID to produce unique
facilities, collecting programs from the first interaction per facility.

Enforcement records (InteractionType='ENFORFNL') in the same layer are
mapped to Violation objects (~4,520 records).

This source only covers Washington (state="WA").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.wa_ecy_mapper import map_enforcement_interaction, map_facility_interaction
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoint — FacilitySiteInteractions
_INTERACTIONS_URL = (
    "https://services.arcgis.com/6lCKYNJLvwTXqrmp/arcgis/rest/services"
    "/ECY/FeatureServer/1/query"
)

class WAECYSource(ArcGISSource):
    """Connector for WA ECY ArcGIS REST FeatureServer."""

    name = "wa_ecy"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from WA ECY FacilitySiteInteractions.

        Deduplicates by FSID — the interactions layer has many rows per
        facility. First interaction per FSID wins (provides address/name).

        WA ECY only covers Washington. Returns empty for non-WA states.
        """
        if state.upper() != "WA":
            logger.warning("Skipping %s (WA ECY is Washington-only)", state)
            return

        seen_ids: set[str] = set()

        features = self._query_features(_INTERACTIONS_URL, "FacilitySiteInteractions")
        for feat in features:
            try:
                facility = map_facility_interaction(feat["attributes"], feat.get("geometry"))
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping interaction: %s", e)

        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from WA ECY enforcement records (ENFORFNL).

        Queries the same FacilitySiteInteractions layer filtered to
        InteractionType='ENFORFNL'. WA ECY only covers Washington.
        """
        if state.upper() != "WA":
            logger.warning("Skipping %s (WA ECY is Washington-only)", state)
            return

        features = self._query_features(
            _INTERACTIONS_URL,
            "Enforcement (ENFORFNL)",
            where="InteractionType='ENFORFNL'",
        )
        count = 0
        for feat in features:
            try:
                violation = map_enforcement_interaction(
                    feat["attributes"], feat.get("geometry")
                )
                count += 1
                yield violation
            except Exception as e:
                logger.warning("Skipping enforcement record: %s", e)

        logger.info("Total violations yielded: %s", count)

