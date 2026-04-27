"""Connector for AZ DEQ (Arizona Department of Environmental Quality) data.

Downloads JSON data from Arizona DEQ's ArcGIS Online FeatureServer services.
Three datasets:
  - Active Underground Storage Tanks (UST) → facilities
  - Municipal Landfills → facilities
  - Superfund Centroids → facilities

Arizona DEQ does not publish a structured violation/enforcement dataset;
violations for Arizona are covered by federal EPA ECHO.

This source only covers Arizona (state="AZ").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.az_deq_mapper import map_ust_facility, map_landfill, map_superfund_site
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS Online FeatureServer endpoints (hosted on services.arcgis.com)
_UST_URL = (
    "https://services.arcgis.com/SzoH1oFM2apCSkx3/arcgis/rest/services"
    "/UST_AGOL/FeatureServer/0/query"
)
_LANDFILLS_URL = (
    "https://services.arcgis.com/SzoH1oFM2apCSkx3/arcgis/rest/services"
    "/Landfills/FeatureServer/0/query"
)
_SUPERFUND_URL = (
    "https://services.arcgis.com/SzoH1oFM2apCSkx3/arcgis/rest/services"
    "/Superfund/FeatureServer/0/query"
)

class AZDEQSource(ArcGISSource):
    """Connector for Arizona DEQ ArcGIS Online FeatureServer services."""

    name = "az_deq"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from AZ DEQ UST, Landfill, and Superfund datasets.

        AZ DEQ only covers Arizona. Returns empty for non-AZ states.
        """
        if state.upper() != "AZ":
            logger.warning("Skipping %s (AZ DEQ is Arizona-only)", state)
            return

        seen_ids: set[str] = set()

        # Underground Storage Tanks (has Latitude/Longitude fields)
        ust_features = self._query_features(_UST_URL, "Underground Storage Tanks")
        for feat in ust_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_ust_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST facility: %s", e)

        ust_count = len(seen_ids)
        logger.info("Unique UST facilities: %s", ust_count)

        # Municipal Landfills (has LATTITUDE/LONGITUDE fields)
        landfill_features = self._query_features(_LANDFILLS_URL, "Municipal Landfills")
        for feat in landfill_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_landfill(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping landfill: %s", e)

        landfill_count = len(seen_ids) - ust_count
        logger.info("Unique landfills: %s", landfill_count)

        # Superfund Centroids (polygon geometry, use centroid via outSR=4326)
        sf_features = self._query_features(
            _SUPERFUND_URL, "Superfund Sites", return_geometry=True
        )
        for feat in sf_features:
            try:
                facility = map_superfund_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping superfund site: %s", e)

        sf_count = len(seen_ids) - ust_count - landfill_count
        logger.info("Unique superfund sites: %s", sf_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """AZ DEQ does not publish structured violation data.

        Violations for Arizona come from federal EPA ECHO (source='echo').
        """
        if state.upper() != "AZ":
            logger.warning("Skipping %s (AZ DEQ is Arizona-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Arizona violations")
        return
        yield  # make this a generator

