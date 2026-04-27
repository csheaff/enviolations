"""Connector for FL DEP CHAZ (Compliance and Hazardous Assessment).

Downloads JSON data from FL DEP's ArcGIS REST services at ca.dep.state.fl.us.
Five datasets — all ingested as facilities:
  - CHAZ/MapServer/0: Closed Hazardous Waste Facilities (~16K) → facilities
  - CHAZ/MapServer/2: Small Quantity Generators (SQGs) (~3.5K) → facilities
  - CHAZ/MapServer/4: Treatment, Storage & Disposal (TSDs) (~100) → facilities
  - CHAZ/MapServer/5: Compliance & Enforcement Tracking (~47K) → facilities
    (comprehensive CHAZ facility master registry, NOT an enforcement/violations layer)

This source only covers Florida (state="FL").

Note: The existing fl_dep connector handles CHAZ/MapServer/1 (LQGs). This
connector covers the remaining CHAZ layers to avoid coupling their ingestion
schedules. Layer 5 is the full CHAZ facility universe and is the primary
source of facility IDs referenced by any future enforcement datasets.
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..models import Facility, Violation
from ..normalize.fl_dep_chaz_mapper import (
    map_chaz_facility,
    map_chaz_layer5_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints
_CLOSED_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/CHAZ/MapServer/0/query"
)
_SQG_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/CHAZ/MapServer/2/query"
)
_TSD_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/CHAZ/MapServer/4/query"
)
_ENFORCEMENT_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/CHAZ/MapServer/5/query"
)


class FLDEPCHAZSource(ArcGISSource):
    """Connector for FL DEP CHAZ (Compliance and Hazardous Assessment)."""

    name = "fl_dep_chaz"
    page_size = 1000

    def _query_features_with_geom(self, url: str, label: str) -> list[dict]:
        """Query ArcGIS and return full feature dicts (attributes + geometry)."""
        return self._query_features(url, label, return_geometry=True)

    @staticmethod
    def _has_id(source_id: str) -> bool:
        """True if source_id has a real ID after the 'chaz-' prefix."""
        return bool(source_id) and source_id != "chaz-"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from CHAZ layers 0, 2, 4, and 5.

        FL DEP CHAZ only covers Florida. Returns empty for non-FL states.
        A HANDLER_ID that appears in multiple layers is deduplicated
        (only the first occurrence is yielded).

        Layer 5 is the comprehensive CHAZ facility master registry covering
        all handler types; it uses different field names than layers 0/2/4.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP CHAZ is Florida-only)", state)
            return

        seen_ids: set[str] = set()

        # Layer 0: Closed Hazardous Waste Facilities
        closed_features = self._query_features_with_geom(
            _CLOSED_URL, "CHAZ Closed HW Facilities"
        )
        for feat in closed_features:
            try:
                facility = map_chaz_facility(
                    feat, default_program="Closed Hazardous Waste"
                )
                if self._has_id(facility.source_id) and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping CHAZ closed facility: %s", e)

        closed_count = len(seen_ids)
        logger.info("Unique CHAZ closed facilities: %s", closed_count)

        # Layer 2: Small Quantity Generators
        sqg_features = self._query_features_with_geom(_SQG_URL, "CHAZ SQGs")
        for feat in sqg_features:
            try:
                facility = map_chaz_facility(
                    feat, default_program="Small Quantity Generator"
                )
                if self._has_id(facility.source_id) and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping CHAZ SQG facility: %s", e)

        sqg_count = len(seen_ids) - closed_count
        logger.info("Unique CHAZ SQG facilities: %s", sqg_count)

        # Layer 4: Treatment, Storage & Disposal
        tsd_features = self._query_features_with_geom(_TSD_URL, "CHAZ TSDs")
        for feat in tsd_features:
            try:
                facility = map_chaz_facility(
                    feat, default_program="Treatment, Storage & Disposal"
                )
                if self._has_id(facility.source_id) and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping CHAZ TSD facility: %s", e)

        tsd_count = len(seen_ids) - closed_count - sqg_count
        logger.info("Unique CHAZ TSD facilities: %s", tsd_count)

        # Layer 5: Compliance & Enforcement Tracking (comprehensive facility master
        # registry — ~47K records covering ALL CHAZ handler types). This layer uses
        # a different field schema (ME_NAME, PHYS_ADDRESS_1, DMS coordinates) and
        # is NOT an enforcement/violations layer despite its name. Ingesting it as
        # facilities ensures violations from any future enforcement source can link
        # to the full CHAZ handler universe.
        layer5_features = self._query_features(
            _ENFORCEMENT_URL, "CHAZ Compliance & Enforcement Tracking (facilities)", return_geometry=False
        )
        for feat in layer5_features:
            try:
                attrs = feat.get("attributes", {})
                facility = map_chaz_layer5_facility(attrs)
                if self._has_id(facility.source_id) and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping CHAZ layer 5 facility: %s", e)

        layer5_count = len(seen_ids) - closed_count - sqg_count - tsd_count
        logger.info("Unique CHAZ layer 5 facilities: %s", layer5_count)
        logger.info("Total unique CHAZ facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """No enforcement violations available from CHAZ ArcGIS layers.

        CHAZ MapServer/5 was originally treated as a violations layer but is
        actually the comprehensive facility master registry. FL DEP CHAZ does
        not currently expose a separate enforcement actions endpoint.
        """
        return
        yield  # make this a generator
