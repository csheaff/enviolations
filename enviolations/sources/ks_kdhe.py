"""Connector for KS KDHE (Kansas Dept of Health and Environment) data.

Downloads JSON data from KS KDHE's ArcGIS REST services at maps.kdhe.ks.gov.
Four facility datasets:
  - Wastewater Permit Facilities (DOE/KDHE_general_programs_ex/MapServer/9)
  - UIC Well Points (DOE/KDHE_general_programs_ex/MapServer/7)
  - BWM Solid Waste (DOE/KDHE_general_programs_ex/MapServer/13)
  - RTK/Tier II (KDEM/BEH_RTK_4_KDEM/MapServer/0)

KS KDHE does not publish structured violation/enforcement data via ArcGIS;
violations for Kansas are covered by federal EPA ECHO.

This source only covers Kansas (state="KS").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ks_kdhe_mapper import (
    map_rtk,
    map_solid_waste,
    map_uic_well,
    map_wastewater,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API URLs
_DOE_BASE = "https://maps.kdhe.ks.gov/kdhe_doe/rest/services/DOE/KDHE_general_programs_ex/MapServer"
_KDEM_BASE = "https://maps.kdhe.ks.gov/kdhe_doe/rest/services/KDEM/BEH_RTK_4_KDEM/MapServer"

_WASTEWATER_URL = f"{_DOE_BASE}/9/query"
_UIC_URL = f"{_DOE_BASE}/7/query"
_SOLID_WASTE_URL = f"{_DOE_BASE}/13/query"
_RTK_URL = f"{_KDEM_BASE}/0/query"

class KSKDHESource(ArcGISSource):
    """Connector for Kansas KDHE ArcGIS REST services."""

    name = "ks_kdhe"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from KS KDHE wastewater, UIC, solid waste, and RTK datasets.

        KS KDHE only covers Kansas. Returns empty for non-KS states.
        """
        if state.upper() != "KS":
            logger.warning("Skipping %s (KS KDHE is Kansas-only)", state)
            return

        seen_ids: set[str] = set()

        # Wastewater Permit Facilities
        ww_features = self._query_features(_WASTEWATER_URL, "Wastewater Permit Facilities")
        for feat in ww_features:
            try:
                facility = map_wastewater(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping wastewater facility: %s", e)

        ww_count = len(seen_ids)
        logger.info("Unique wastewater facilities: %s", ww_count)

        # UIC Well Points (multiple wells per facility — dedup by FACILITY_ID)
        uic_features = self._query_features(_UIC_URL, "UIC Well Points")
        for feat in uic_features:
            try:
                facility = map_uic_well(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UIC well: %s", e)

        uic_count = len(seen_ids) - ww_count
        logger.info("Unique UIC facilities: %s", uic_count)

        # BWM Solid Waste
        sw_features = self._query_features(_SOLID_WASTE_URL, "BWM Solid Waste")
        for feat in sw_features:
            try:
                facility = map_solid_waste(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping solid waste: %s", e)

        sw_count = len(seen_ids) - ww_count - uic_count
        logger.info("Unique solid waste facilities: %s", sw_count)

        # RTK/Tier II (different MapServer)
        rtk_features = self._query_features(_RTK_URL, "RTK/Tier II")
        for feat in rtk_features:
            try:
                facility = map_rtk(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping RTK facility: %s", e)

        rtk_count = len(seen_ids) - ww_count - uic_count - sw_count
        logger.info("Unique RTK/Tier II facilities: %s", rtk_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """KS KDHE does not publish structured violation data via ArcGIS.

        Violations for Kansas come from federal EPA ECHO (source='echo').
        """
        if state.upper() != "KS":
            logger.warning("Skipping %s (KS KDHE is Kansas-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Kansas violations")
        return
        yield  # make this a generator

