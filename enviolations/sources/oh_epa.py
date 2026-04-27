"""Connector for Ohio EPA environmental data.

Downloads JSON data from Ohio EPA's ArcGIS REST services at
geo.epa.ohio.gov. Five datasets:
  - NPDES Select Facilities (water permits) → facilities
  - DMWM Regulated Facilities (waste management) → facilities
  - DERR Sites (environmental cleanup) → facilities
  - Spills2_OpenData/CurrentYear (Layer 0) → facilities + violations
  - Spills2_OpenData/PreviousYears (Layer 1) → facilities + violations (15.7K)

This source only covers Ohio (state="OH").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation  # noqa: F401 (Violation needed for return type)
from ..normalize.oh_epa_mapper import (
    map_derr_site, map_dmwm_facility, map_npdes_facility,
    map_spill_facility, map_spill_violation,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints
_NPDES_URL = (
    "https://geo.epa.ohio.gov/arcgis/rest/services"
    "/Hosted/NPDES_SELECT_FACS/FeatureServer/0/query"
)
_DMWM_URL = (
    "https://geo.epa.ohio.gov/arcgis/rest/services"
    "/WasteMgmt/DMWM_Regulated_Facilities/MapServer/0/query"
)
_DERR_URL = (
    "https://geo.epa.ohio.gov/arcgis/rest/services"
    "/Hosted/DERR_SITES_POINTS/FeatureServer/0/query"
)
_SPILLS_CURRENT_URL = (
    "https://geo.epa.ohio.gov/arcgis/rest/services"
    "/EmergResponse/Spills2_OpenData/MapServer/0/query"
)
_SPILLS_PREVIOUS_URL = (
    "https://geo.epa.ohio.gov/arcgis/rest/services"
    "/EmergResponse/Spills2_OpenData/MapServer/1/query"
)

class OhioEPASource(ArcGISSource):
    """Connector for Ohio EPA ArcGIS REST services."""

    name = "oh_epa"

    def __init__(self) -> None:
        super().__init__()
        self._spill_features: list[dict] | None = None

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from Ohio EPA NPDES, DMWM, and DERR datasets.

        Ohio EPA only covers Ohio. Returns empty for non-OH states.
        """
        if state.upper() != "OH":
            logger.warning("Skipping %s (Ohio EPA is Ohio-only)", state)
            return

        seen_ids: set[str] = set()

        # NPDES Select Facilities (water permits)
        npdes_features = self._query_features(_NPDES_URL, "NPDES Select Facilities")
        for feat in npdes_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_npdes_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping NPDES facility: %s", e)

        npdes_count = len(seen_ids)
        logger.info("Unique NPDES facilities: %s", npdes_count)

        # DMWM Regulated Facilities (waste management)
        dmwm_features = self._query_features(_DMWM_URL, "DMWM Regulated Facilities")
        for feat in dmwm_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_dmwm_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping DMWM facility: %s", e)

        dmwm_count = len(seen_ids) - npdes_count
        logger.info("Unique DMWM facilities: %s", dmwm_count)

        # DERR Sites (environmental cleanup)
        derr_features = self._query_features(_DERR_URL, "DERR Sites")
        for feat in derr_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_derr_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping DERR site: %s", e)

        derr_count = len(seen_ids) - npdes_count - dmwm_count
        logger.info("Unique DERR sites: %s", derr_count)

        # Spills (current year + previous years)
        prev_count = len(seen_ids)
        current_features = self._query_features(_SPILLS_CURRENT_URL, "Spills Current Year")
        previous_features = self._query_features(_SPILLS_PREVIOUS_URL, "Spills Previous Years")
        all_spill_features = current_features + previous_features
        self._spill_features = all_spill_features  # cache for fetch_violations

        for feat in all_spill_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_spill_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping spill facility: %s", e)

        spill_count = len(seen_ids) - prev_count
        logger.info("Unique spill facilities: %s", spill_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from Ohio EPA spill incident reports."""
        if state.upper() != "OH":
            return

        spill_features = self._spill_features
        if spill_features is None:
            current_features = self._query_features(_SPILLS_CURRENT_URL, "Spills Current Year")
            previous_features = self._query_features(_SPILLS_PREVIOUS_URL, "Spills Previous Years")
            spill_features = current_features + previous_features

        spill_count = 0
        for feat in spill_features:
            attrs = feat.get("attributes", feat)
            try:
                violation = map_spill_violation(attrs)
                if violation.source_id:
                    yield violation
                    spill_count += 1
            except Exception as e:
                logger.warning("Skipping spill violation: %s", e)

        logger.info("Spill violations: %s", spill_count)

