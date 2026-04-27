"""Connector for WY DEQ (Wyoming Department of Environmental Quality) data.

Downloads JSON data from Wyoming DEQ's ArcGIS MapServer at gis.deq.wyo.gov.
Four datasets:
  - WYPDES Outfalls (WYPDES_OUTFALLS/MapServer/0): ~16K water discharge permits
    (deduped by WYPermitNu)
  - Municipal/Industrial/C&D Solid Waste Landfills (WDEQ_DATA/MapServer/289-291): ~57 sites
  - STP Active Cleanup Sites (WDEQ_DATA/MapServer/279): ~569 tank cleanup sites
  - VRP/Brownfield Sites (VRP_Map/MapServer/1): ~82 voluntary remediation sites

Wyoming DEQ does not publish structured violation/enforcement data via ArcGIS;
violations for Wyoming are covered by federal EPA ECHO.

Note: WDEQ_DATA layers 289 and 279, and VRP_Map layer 1, do not support
pagination but return all records in a single query (small datasets).

This source only covers Wyoming (state="WY").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.wy_deq_mapper import (
    map_wypdes,
    map_landfill,
    map_stp_site,
    map_vrp_site,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# WYPDES water discharge outfalls (supports pagination)
_WYPDES_URL = (
    "https://gis.deq.wyo.gov/arcgis/rest/services"
    "/WYPDES_OUTFALLS/MapServer/0/query"
)

# WDEQ_DATA MapServer layers (no pagination support, all fit in one query)
_WDEQ_DATA_BASE = (
    "https://gis.deq.wyo.gov/arcgis/rest/services"
    "/WDEQ_DATA/MapServer"
)
_LANDFILLS_URL = f"{_WDEQ_DATA_BASE}/289/query"
_STP_URL = f"{_WDEQ_DATA_BASE}/279/query"

# VRP (Voluntary Remediation Program) sites (no pagination support)
_VRP_URL = (
    "https://gis.deq.wyo.gov/arcgis/rest/services"
    "/VRP_Map/MapServer/1/query"
)

class WYDEQSource(ArcGISSource):
    """Connector for Wyoming DEQ ArcGIS MapServer services."""

    name = "wy_deq"
    page_size = 1000

    def _query_features_single(self, url: str, label: str) -> list[dict]:
        """Query an ArcGIS layer without pagination (for small datasets).

        Delegates to base _query_features — servers that don't support pagination
        naturally return all records on the first page.
        """
        return self._query_features(url, label, return_geometry=False)

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "WY":
            logger.warning("Skipping %s (WY DEQ is Wyoming-only)", state)
            return

        seen_ids: set[str] = set()

        # WYPDES Outfalls (paginated, dedup by WYPermitNu)
        wypdes_features = self._query_features(
            _WYPDES_URL, "WYPDES Outfalls", return_geometry=False
        )
        for feat in wypdes_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_wypdes(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping WYPDES: %s", e)

        wypdes_count = len(seen_ids)
        logger.info("Unique WYPDES facilities: %s", wypdes_count)

        # Solid Waste Landfills (single query, no pagination)
        lf_features = self._query_features_single(_LANDFILLS_URL, "Landfills")
        for feat in lf_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_landfill(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping landfill: %s", e)

        lf_count = len(seen_ids) - wypdes_count
        logger.info("Unique landfills: %s", lf_count)

        # STP Active Cleanup Sites (single query, no pagination)
        stp_features = self._query_features_single(_STP_URL, "STP Cleanup Sites")
        for feat in stp_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_stp_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping STP site: %s", e)

        stp_count = len(seen_ids) - wypdes_count - lf_count
        logger.info("Unique STP sites: %s", stp_count)

        # VRP/Brownfield Sites (single query, no pagination)
        vrp_features = self._query_features_single(_VRP_URL, "VRP Sites")
        for feat in vrp_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_vrp_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping VRP site: %s", e)

        vrp_count = len(seen_ids) - wypdes_count - lf_count - stp_count
        logger.info("Unique VRP sites: %s", vrp_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "WY":
            logger.warning("Skipping %s (WY DEQ is Wyoming-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Wyoming violations")
        return
        yield  # make this a generator

