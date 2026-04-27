"""Connector for NM NMED (New Mexico Environment Department) data.

Downloads facility and violation data from NMED's ArcGIS servers.

Three facility datasets:
  - Air Facilities (mercator.env.nm.gov aqb/air_facilities): layers 0-2 (permitted)
    Layers 3-6 (No Permit, NOE, NOI, Nonactive) are skipped — 1.49M low-value
    records that aren't active compliance obligations and cause 30+ min ingest.
  - Petroleum Storage Tank Facilities (x-23.env.nm.gov pstb): ~680 PST facilities
  - Brownfields (mercator.env.nm.gov gwqb/Brownfields): cleanup sites

Two violation datasets:
  - Leaking PST Releases (mercator.env.nm.gov pstb): ~3,063 release records
  - Delivery Prohibition (mercator.env.nm.gov pstb): ~167 prohibition records

This source only covers New Mexico (state="NM").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.nm_nmed_mapper import (
    map_air_facility,
    map_brownfield,
    map_delivery_prohibition,
    map_dp_facility,
    map_lpst_facility,
    map_lpst_release,
    map_pst_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_AIR_BASE = "https://mercator.env.nm.gov/server/rest/services/aqb/air_facilities/FeatureServer"
_PST_URL = "https://mercator.env.nm.gov/server/rest/services/pstb/petroleum_storage_tank_facilities/FeatureServer/0/query"
_BROWNFIELD_URL = "https://mercator.env.nm.gov/server/rest/services/gwqb/Brownfields/FeatureServer/0/query"

_LPST_RELEASES_URL = (
    "https://mercator.env.nm.gov/server/rest/services/pstb"
    "/leaking_petroleum_storage_tank_releases_priority/FeatureServer/0/query"
)
_DELIVERY_PROHIBITION_URL = (
    "https://mercator.env.nm.gov/server/rest/services/pstb"
    "/delivery_prohibition/FeatureServer/0/query"
)

# Air facility layers 0-2 only: permitted facilities with active compliance
# obligations.  Layers 3-6 (No Permit Required: 91K, NOE: 20K, NOI: 775K,
# Nonactive: 608K) are skipped — they add 1.49M records that aren't regulated
# and push ingest past the 1800s timeout.
_AIR_LAYERS = [0, 1, 2]
_AIR_LAYER_NAMES = ["Major-Title V", "Minor", "Synthetic Minor"]

class NMNMEDSource(ArcGISSource):
    """Connector for New Mexico NMED ArcGIS services."""

    name = "nm_nmed"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "NM":
            logger.warning("Skipping %s (NM NMED is New Mexico-only)", state)
            return

        seen_ids: set[str] = set()

        # Air Facilities (layers 0-6)
        for layer_id, layer_name in zip(_AIR_LAYERS, _AIR_LAYER_NAMES):
            url = f"{_AIR_BASE}/{layer_id}/query"
            features = self._query_features(url, f"Air - {layer_name}")
            for feat in features:
                try:
                    facility = map_air_facility(feat, layer_name)
                    if facility.source_id and facility.source_id not in seen_ids:
                        seen_ids.add(facility.source_id)
                        yield facility
                except Exception as e:
                    logger.warning("Skipping air facility: %s", e)

        air_count = len(seen_ids)
        logger.info("Unique air facilities: %s", air_count)

        # Petroleum Storage Tank Facilities
        try:
            pst_features = self._query_features(_PST_URL, "PST Facilities")
        except Exception as e:
            logger.warning("PST facility fetch failed, skipping: %s", e)
            pst_features = []
        for feat in pst_features:
            try:
                facility = map_pst_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping PST facility: %s", e)

        pst_count = len(seen_ids) - air_count
        logger.info("Unique PST facilities: %s", pst_count)

        # Always create pst-* facility stubs from LPST releases and delivery
        # prohibitions so violations can link even when the PST FeatureServer
        # is unavailable. Stubs for facilities already in seen_ids are skipped.
        # Wrap in try/except so a transient server error doesn't silently drop
        # all PST stubs (which would leave violations orphaned after re-ingest).
        try:
            lpst_features = self._query_features(_LPST_RELEASES_URL, "LPST (for facilities)")
        except Exception as e:
            logger.warning("LPST facility fetch failed, skipping stubs: %s", e)
            lpst_features = []
        lpst_fac_count = 0
        for feat in lpst_features:
            try:
                facility = map_lpst_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    lpst_fac_count += 1
            except Exception:
                pass
        logger.info("LPST-derived PST facilities: %s", lpst_fac_count)

        try:
            dp_features = self._query_features(_DELIVERY_PROHIBITION_URL, "Delivery Prohibition (for facilities)")
        except Exception as e:
            logger.warning("DP facility fetch failed, skipping stubs: %s", e)
            dp_features = []
        dp_fac_count = 0
        for feat in dp_features:
            try:
                facility = map_dp_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    dp_fac_count += 1
            except Exception:
                pass
        logger.info("DP-derived PST facilities: %s", dp_fac_count)

        # Brownfields
        try:
            bf_features = self._query_features(_BROWNFIELD_URL, "Brownfields")
        except Exception as e:
            logger.warning("Brownfield facility fetch failed, skipping: %s", e)
            bf_features = []
        for feat in bf_features:
            try:
                facility = map_brownfield(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping brownfield: %s", e)

        bf_count = len(seen_ids) - air_count - pst_count
        logger.info("Unique brownfields: %s", bf_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "NM":
            logger.warning("Skipping %s (NM NMED is New Mexico-only)", state)
            return

        seen_ids: set[str] = set()

        # Leaking PST Releases
        try:
            lpst_features = self._query_features(_LPST_RELEASES_URL, "LPST Releases")
        except Exception as e:
            logger.error("Failed to fetch LPST releases: %s", e)
            lpst_features = []
        for feat in lpst_features:
            try:
                violation = map_lpst_release(feat)
                if violation.source_id and violation.source_id not in seen_ids:
                    seen_ids.add(violation.source_id)
                    yield violation
            except Exception as e:
                logger.warning("Skipping LPST release: %s", e)

        lpst_count = len(seen_ids)
        logger.info("Unique LPST releases: %s", lpst_count)

        # Delivery Prohibitions
        try:
            dp_features = self._query_features(_DELIVERY_PROHIBITION_URL, "Delivery Prohibitions")
        except Exception as e:
            logger.error("Failed to fetch delivery prohibitions: %s", e)
            dp_features = []
        for feat in dp_features:
            try:
                violation = map_delivery_prohibition(feat)
                if violation.source_id and violation.source_id not in seen_ids:
                    seen_ids.add(violation.source_id)
                    yield violation
            except Exception as e:
                logger.warning("Skipping delivery prohibition: %s", e)

        dp_count = len(seen_ids) - lpst_count
        logger.info("Unique delivery prohibitions: %s", dp_count)
        logger.info("Total unique violations: %s", len(seen_ids))

