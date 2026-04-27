"""Connector for EPA PFAS Analytic Tools ArcGIS FeatureServer.

Downloads facility-level PFAS data from EPA's national PFAS Analytic Tools
layers. Single source covering all 50 states + DC.

FeatureServer: https://services.arcgis.com/cJ9YHowT8TU7DUyn/ArcGIS/rest/services/PFAS_Analytic_Tools_Layers/FeatureServer

Priority layers (facility-level):
  - Layer 2:  Superfund PFAS sites (~465)
  - Layer 3:  Industry sectors (~210K)
  - Layer 4:  TRI offsite transfers (~710)
  - Layer 5:  TRI waste management (~1,895)
  - Layer 9:  eManifest destinations (~535)
  - Layer 10: eManifest generators (~535)
  - Layer 12: Spills/ERNS (~1,408)
  - Layer 13: Federal/DoD sites (~761)
  - Layer 16: Chemical Data Reporting (~1,643)

Deferred (sample-level, handled separately):
  - Layer 1:  UCMR monitoring (2.1M records) -- CIV-740
  - Layer 17: Water Quality Portal (484K records)

Note: The State field in this dataset has a leading space (e.g. " NH" not "NH").
The WHERE clause must include the leading space for filtering to work.
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.epa_pfas_mapper import (
    map_superfund_facility,
    map_spill_facility,
    map_dod_facility,
    map_industry_facility,
    map_cdr_facility,
    map_tri_offsite_facility,
    map_tri_waste_facility,
    map_emanifest_dest_facility,
    map_emanifest_gen_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE_URL = (
    "https://services.arcgis.com/cJ9YHowT8TU7DUyn/ArcGIS/rest/services"
    "/PFAS_Analytic_Tools_Layers/FeatureServer"
)

# (layer_id, label, state_field, mapper_func)
# Most layers use "State" but eManifest layers use different field names.
_LAYERS: list[tuple[int, str, str, object]] = [
    (2,  "Superfund PFAS",          "State",                     map_superfund_facility),
    (12, "Spills/ERNS",             "State",                     map_spill_facility),
    (13, "Federal/DoD Sites",       "State",                     map_dod_facility),
    (3,  "Industry Sectors",        "State",                     map_industry_facility),
    (16, "Chemical Data Reporting",  "State",                     map_cdr_facility),
    (4,  "TRI Offsite Transfers",   "State",                     map_tri_offsite_facility),
    (5,  "TRI Waste Management",    "State",                     map_tri_waste_facility),
    (9,  "eManifest Destinations",  "DES_FAC_LOCATION_STATE",    map_emanifest_dest_facility),
    (10, "eManifest Generators",    "GENERATOR_LOCATION_STATE",  map_emanifest_gen_facility),
]


class EPAPFASSource(ArcGISSource):
    """Connector for EPA PFAS Analytic Tools ArcGIS FeatureServer."""

    name = "epa_pfas"
    page_size = 2000
    rate_limit_delay = 1.0

    def _query_layer(
        self, layer_id: int, label: str, state_field: str, state: str
    ) -> list[dict]:
        """Query a single PFAS layer for a given state."""
        url = f"{_BASE_URL}/{layer_id}/query"
        # State values have a leading space in this dataset (e.g. " NH")
        where = f"{state_field}=' {state.upper()}'"
        return self._query_features(
            url,
            f"PFAS {label} ({state})",
            where=where,
            return_geometry=True,
        )

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield PFAS facilities for a given state across all priority layers."""
        state = state.upper()
        seen_ids: set[str] = set()
        total = 0

        for layer_id, label, state_field, mapper_func in _LAYERS:
            features = self._query_layer(layer_id, label, state_field, state)
            layer_count = 0

            for feat in features:
                try:
                    facility = mapper_func(feat)
                    if facility.source_id and facility.source_id not in seen_ids:
                        seen_ids.add(facility.source_id)
                        yield facility
                        layer_count += 1
                except Exception as e:
                    logger.warning("Skipping %s feature: %s", label, e)

            logger.info("%s: %s unique %s facilities", state, layer_count, label)
            total += layer_count

        logger.info("%s: %s total PFAS facilities", state, total)

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """PFAS Analytic Tools has no violation data.

        Violations for these facilities come from other EPA programs
        (RCRA, CWA, CAA) via cross-source entity resolution.
        """
        logger.info("No violation data in EPA PFAS; violations come from other EPA programs")
        return
        yield  # make this a generator
