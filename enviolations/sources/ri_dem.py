"""Connector for RI DEM (Rhode Island Department of Environmental Management) data.

Downloads JSON data from RIGIS ArcGIS services at services2.arcgis.com and
risegis.ri.gov. Two facility datasets:
  - Active Solid Waste Facility Sites (RIGIS ArcGIS Online): landfills,
    composting facilities, transfer stations
  - Regulated Facilities - Brownfields (risegis.ri.gov FeatureServer/23):
    brownfield sites under investigation or remediation

RI DEM does not publish structured violation/enforcement data via ArcGIS;
violations for Rhode Island are covered by federal EPA ECHO.

This source only covers Rhode Island (state="RI").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ri_dem_mapper import map_brownfield, map_solid_waste
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# RIGIS ArcGIS Online - Active Solid Waste Facility Sites
_SOLID_WASTE_URL = (
    "https://services2.arcgis.com/S8zZg9pg23JUEexQ/arcgis/rest/services"
    "/Active_Solid_Waste_Facility_Sites/FeatureServer/0/query"
)

# RIDEM Regulated Facilities - Brownfields
_BROWNFIELD_URL = (
    "https://risegis.ri.gov/hosting/rest/services"
    "/RIDEM/Regulated_Facilities/FeatureServer/23/query"
)

class RIDEMSource(ArcGISSource):
    """Connector for Rhode Island DEM ArcGIS REST services."""

    name = "ri_dem"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "RI":
            logger.warning("Skipping %s (RI DEM is Rhode Island-only)", state)
            return

        seen_ids: set[str] = set()

        # Active Solid Waste Facility Sites
        sw_features = self._query_features(_SOLID_WASTE_URL, "Active Solid Waste Sites")
        for feat in sw_features:
            try:
                facility = map_solid_waste(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping solid waste site: %s", e)

        sw_count = len(seen_ids)
        logger.info("Unique solid waste sites: %s", sw_count)

        # Brownfield Sites
        bf_features = self._query_features(_BROWNFIELD_URL, "Brownfield Sites")
        for feat in bf_features:
            try:
                facility = map_brownfield(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping brownfield site: %s", e)

        bf_count = len(seen_ids) - sw_count
        logger.info("Unique brownfield sites: %s", bf_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "RI":
            logger.warning("Skipping %s (RI DEM is Rhode Island-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Rhode Island violations")
        return
        yield  # make this a generator

