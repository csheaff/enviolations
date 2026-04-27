"""Connector for FL DEP STCM (Storage Tank Contamination Monitoring).

Downloads JSON data from FL DEP's ArcGIS REST services at ca.dep.state.fl.us.
Three datasets:
  - DWM_STCM/MapServer/1: Registered Tanks (~74K) → facilities
  - DWM_STCM/MapServer/2: PCTS Discharges (~38K) → violations + facility stubs
  - DWM_STCM/MapServer/4: Drycleaning Solvent Program Sites (~1.3K) → facilities

This source only covers Florida (state="FL").
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..models import Facility, Violation
from ..normalize.fl_dep_stcm_mapper import (
    map_dryclean_facility,
    map_pcts_violation,
    map_pcts_violation_facility,
    map_stcm_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints
_STCM_TANKS_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/DWM_STCM/MapServer/1/query"
)
_PCTS_DISCHARGES_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/DWM_STCM/MapServer/2/query"
)
_DRYCLEAN_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/DWM_STCM/MapServer/4/query"
)


class FLDEPSTCMSource(ArcGISSource):
    """Connector for FL DEP Storage Tank Contamination Monitoring."""

    name = "fl_dep_stcm"
    page_size = 1000

    def _query_attrs(self, url: str, label: str) -> list[dict]:
        """Query ArcGIS and return just the attributes dicts (no geometry)."""
        features = self._query_features(url, label, return_geometry=False)
        return [feat.get("attributes", {}) for feat in features]

    def _get_pcts_attrs(self) -> list[dict]:
        """Fetch PCTS discharge attrs, caching to avoid double-fetch."""
        if not hasattr(self, "_pcts_cache"):
            self._pcts_cache = self._query_attrs(
                _PCTS_DISCHARGES_URL, "PCTS Discharges"
            )
        return self._pcts_cache

    @staticmethod
    def _has_id(source_id: str, prefix: str) -> bool:
        """True if source_id has a real ID after the prefix (not just 'stcm-')."""
        return bool(source_id) and source_id != prefix

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from STCM Registered Tanks and PCTS Discharges.

        FL DEP STCM only covers Florida. Returns empty for non-FL states.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP STCM is Florida-only)", state)
            return

        seen_ids: set[str] = set()

        # Registered Tanks
        tank_attrs = self._query_attrs(_STCM_TANKS_URL, "STCM Registered Tanks")
        for attrs in tank_attrs:
            try:
                facility = map_stcm_facility(attrs)
                if self._has_id(facility.source_id, "stcm-") and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping STCM tank facility: %s", e)

        tank_count = len(seen_ids)
        logger.info("Unique STCM tank facilities: %s", tank_count)

        # PCTS Discharges — create facility stubs for any new FACILITY_IDs
        pcts_fac_count = 0
        for attrs in self._get_pcts_attrs():
            try:
                facility = map_pcts_violation_facility(attrs)
                if self._has_id(facility.source_id, "stcm-") and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    pcts_fac_count += 1
            except Exception as e:
                logger.warning("Skipping PCTS facility stub: %s", e)

        logger.info("New facilities from PCTS discharges: %s", pcts_fac_count)

        # Drycleaning Solvent Program Sites
        dryclean_attrs = self._query_attrs(
            _DRYCLEAN_URL, "Drycleaning Solvent Program Sites"
        )
        dryclean_count = 0
        for attrs in dryclean_attrs:
            try:
                facility = map_dryclean_facility(attrs)
                if (
                    self._has_id(facility.source_id, "dryclean-")
                    and facility.source_id not in seen_ids
                ):
                    seen_ids.add(facility.source_id)
                    yield facility
                    dryclean_count += 1
            except Exception as e:
                logger.warning("Skipping drycleaning facility: %s", e)

        logger.info("Unique drycleaning facilities: %s", dryclean_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from PCTS Discharges.

        FL DEP STCM only covers Florida. Returns empty for non-FL states.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP STCM is Florida-only)", state)
            return

        count = 0
        for attrs in self._get_pcts_attrs():
            try:
                violation = map_pcts_violation(attrs)
                if not self._has_id(violation.source_id, "pcts-"):
                    continue
                yield violation
                count += 1
            except Exception as e:
                logger.warning("Skipping PCTS violation: %s", e)

        logger.info("PCTS discharge violations: %s", count)
