"""Connector for IN IDEM (Indiana Department of Environmental Management) data.

Downloads JSON data from Indiana's ArcGIS FeatureServer at gisdata.in.gov.
Facility datasets:
  - NPDES Facilities → facilities
  - Underground Storage Tanks → facilities
  - State Cleanup Sites → facilities
  - Brownfields → facilities
  - Waste Disposal Storage and Handling → facilities
Violation datasets:
  - Spills (IDEM_Land_Sites/1700) → facilities + violations (10.2K)
  - LUST Sites (IDEM_Land_Sites/1600) → facilities + violations (8.2K)

This source only covers Indiana (state="IN").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.in_idem_mapper import (
    map_brownfield,
    map_cleanup,
    map_lust_facility, map_lust_violation,
    map_npdes,
    map_spill_facility, map_spill_violation,
    map_ust,
    map_waste,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API URLs
_BASE = "https://gisdata.in.gov/server/rest/services/Hosted"
_NPDES_URL = f"{_BASE}/National_Pollutant_Discharge_Elimination_System/FeatureServer/3401/query"
_UST_URL = f"{_BASE}/Underground_Storage_Tanks/FeatureServer/2041/query"
_CLEANUP_URL = f"{_BASE}/State_Cleanup_Sites/FeatureServer/2250/query"
_BROWNFIELD_URL = f"{_BASE}/Brownfields/FeatureServer/2020/query"
_WASTE_URL = f"{_BASE}/Waste_Disposal_Storage_and_Handling/FeatureServer/2090/query"
_SPILLS_URL = f"{_BASE}/IDEM_Land_Sites/FeatureServer/1700/query"
_LUST_URL = f"{_BASE}/IDEM_Land_Sites/FeatureServer/1600/query"

class INIDEMSource(ArcGISSource):
    """Connector for Indiana IDEM ArcGIS FeatureServer services."""

    name = "in_idem"
    page_size = 1000

    def __init__(self) -> None:
        super().__init__()
        self._spill_features: list[dict] | None = None
        self._lust_features: list[dict] | None = None

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from IN IDEM NPDES, UST, cleanup, brownfield, and waste datasets.

        IN IDEM only covers Indiana. Returns empty for non-IN states.
        """
        if state.upper() != "IN":
            logger.warning("Skipping %s (IN IDEM is Indiana-only)", state)
            return

        seen_ids: set[str] = set()

        # NPDES Facilities
        npdes_features = self._query_features(_NPDES_URL, "NPDES Facilities")
        for feat in npdes_features:
            try:
                facility = map_npdes(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping NPDES facility: %s", e)

        npdes_count = len(seen_ids)
        logger.info("Unique NPDES facilities: %s", npdes_count)

        # Underground Storage Tanks
        ust_features = self._query_features(_UST_URL, "Underground Storage Tanks")
        for feat in ust_features:
            try:
                facility = map_ust(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST: %s", e)

        ust_count = len(seen_ids) - npdes_count
        logger.info("Unique UST facilities: %s", ust_count)

        # State Cleanup Sites
        cleanup_features = self._query_features(_CLEANUP_URL, "State Cleanup Sites")
        for feat in cleanup_features:
            try:
                facility = map_cleanup(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping cleanup site: %s", e)

        cleanup_count = len(seen_ids) - npdes_count - ust_count
        logger.info("Unique cleanup sites: %s", cleanup_count)

        # Brownfields
        bf_features = self._query_features(_BROWNFIELD_URL, "Brownfields")
        for feat in bf_features:
            try:
                facility = map_brownfield(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping brownfield: %s", e)

        bf_count = len(seen_ids) - npdes_count - ust_count - cleanup_count
        logger.info("Unique brownfields: %s", bf_count)

        # Waste Disposal Storage and Handling
        waste_features = self._query_features(_WASTE_URL, "Waste Disposal")
        for feat in waste_features:
            try:
                facility = map_waste(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping waste facility: %s", e)

        waste_count = len(seen_ids) - npdes_count - ust_count - cleanup_count - bf_count
        logger.info("Unique waste facilities: %s", waste_count)

        # Spills
        prev_count = len(seen_ids)
        spill_features = self._query_features(_SPILLS_URL, "Spills")
        self._spill_features = spill_features  # cache for fetch_violations
        for feat in spill_features:
            try:
                facility = map_spill_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping spill facility: %s", e)

        spill_count = len(seen_ids) - prev_count
        logger.info("Unique spill facilities: %s", spill_count)

        # LUST Sites
        prev_count = len(seen_ids)
        lust_features = self._query_features(_LUST_URL, "LUST Sites")
        self._lust_features = lust_features  # cache for fetch_violations
        for feat in lust_features:
            try:
                facility = map_lust_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping LUST facility: %s", e)

        lust_count = len(seen_ids) - prev_count
        logger.info("Unique LUST facilities: %s", lust_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from IN IDEM Spills and LUST Sites."""
        if state.upper() != "IN":
            return

        # Spills
        spill_features = self._spill_features
        if spill_features is None:
            spill_features = self._query_features(_SPILLS_URL, "Spills")

        spill_count = 0
        for feat in spill_features:
            try:
                violation = map_spill_violation(feat)
                if violation.source_id:
                    yield violation
                    spill_count += 1
            except Exception as e:
                logger.warning("Skipping spill violation: %s", e)

        logger.info("Spill violations: %s", spill_count)

        # LUST Sites
        lust_features = self._lust_features
        if lust_features is None:
            lust_features = self._query_features(_LUST_URL, "LUST Sites")

        lust_count = 0
        for feat in lust_features:
            try:
                violation = map_lust_violation(feat)
                if violation.source_id:
                    yield violation
                    lust_count += 1
            except Exception as e:
                logger.warning("Skipping LUST violation: %s", e)

        logger.info("LUST violations: %s", lust_count)
        logger.info("Total violations: %s", spill_count + lust_count)

