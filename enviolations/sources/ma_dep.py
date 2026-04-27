"""Connector for MA DEP (Massachusetts Dept. of Environmental Protection).

Downloads JSON data from MassGIS ArcGIS FeatureServer endpoints. Three datasets:
  - DEP Air Facilities → facilities (1,447 records)
  - Solid Waste Disposal Land → facilities (612 records)
  - Public Water Supply Sources → facilities (3,990 records)

MA DEP does not expose structured violation data via ArcGIS;
violations for Massachusetts come from federal EPA ECHO (already ingested).

This source only covers Massachusetts (state="MA").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ma_dep_mapper import map_air_facility, map_pws_source, map_sw_disposal
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS FeatureServer endpoints
_AIR_FACILITIES_URL = (
    "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services"
    "/AGOL/DEP_Air_Facilities_(view)/FeatureServer/0/query"
)

_SW_DISPOSAL_URL = (
    "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services"
    "/AGOL/DEP_SW_Disposal_Land/FeatureServer/0/query"
)

_PWS_URL = (
    "https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services"
    "/PWS_gdb/FeatureServer/0/query"
)

class MADEPSource(ArcGISSource):
    """Connector for MA DEP ArcGIS FeatureServer endpoints."""

    name = "ma_dep"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from MA DEP datasets.

        MA DEP only covers Massachusetts. Returns empty for non-MA states.
        """
        if state.upper() != "MA":
            logger.warning("Skipping %s (MA DEP is Massachusetts-only)", state)
            return

        seen_ids: set[str] = set()

        # 1. Air Facilities
        air_features = self._query_features(_AIR_FACILITIES_URL, "Air Facilities")
        for feat in air_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_air_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air facility: %s", e)

        # 2. Solid Waste Disposal
        sw_features = self._query_features(_SW_DISPOSAL_URL, "Solid Waste Disposal")
        for feat in sw_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_sw_disposal(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping solid waste site: %s", e)

        # 3. Public Water Supply Sources
        pws_features = self._query_features(_PWS_URL, "Public Water Supply Sources")
        for feat in pws_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_pws_source(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping PWS source: %s", e)

        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """MA DEP does not publish structured violation data via ArcGIS.

        Violations for Massachusetts come from federal EPA ECHO (source='echo').
        """
        if state.upper() != "MA":
            logger.warning("Skipping %s (MA DEP is Massachusetts-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Massachusetts violations")
        return
        yield  # make this a generator

