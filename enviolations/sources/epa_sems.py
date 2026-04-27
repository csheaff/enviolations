"""Connector for EPA SEMS (Superfund Enterprise Management System) data.

Downloads Superfund site data from EPA's FRS_INTERESTS ArcGIS MapServer.
~14,809 sites nationally covering both NPL and non-NPL Superfund sites.

This is a federal source covering all 50 states + DC.

Endpoint: https://geodata.epa.gov/arcgis/rest/services/OEI/FRS_INTERESTS/MapServer/21

SEMS is the successor to CERCLIS and is the primary EPA database for tracking
Superfund sites. Adding this source closes the biggest ASTM E1527-21 compliance
gap identified by consultant reviewers.

No violation data — SEMS is a site inventory. Violations for Superfund sites
come from other EPA programs (RCRA, CWA, CAA) via cross-source entity resolution.
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.epa_sems_mapper import map_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_SEMS_URL = (
    "https://geodata.epa.gov/arcgis/rest/services"
    "/OEI/FRS_INTERESTS/MapServer/21/query"
)

class EPASEMSSource(ArcGISSource):
    """Connector for EPA SEMS ArcGIS MapServer."""

    name = "epa_sems"
    page_size = 5000

    def _query_state(self, state: str) -> list[dict]:
        """Query SEMS features for a single state."""
        return self._query_features(
            _SEMS_URL,
            f"SEMS ({state})",
            where=f"STATE_CODE='{state.upper()}'",
            return_geometry=False,
        )

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield SEMS Superfund facilities for a given state."""
        features = self._query_state(state)
        seen_ids: set[str] = set()

        for feat in features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping facility: %s", e)

        logger.info("%s: %s SEMS facilities", state, len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """SEMS has no violation data.

        Violations for Superfund sites come from RCRA, CWA, CAA, and state
        sources via cross-source entity resolution using shared REGISTRY_ID.
        """
        logger.info("No violation data in SEMS; Superfund violations come from other EPA programs")
        return
        yield  # make this a generator

