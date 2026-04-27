"""Connector for CA DTSC (California Dept. of Toxic Substances Control).

Downloads JSON data from CA DTSC's ArcGIS REST services. Two datasets
from the EnviroStor system:
  - Cleanup Sites → facilities + violations (active sites)
  - Hazardous Waste Sites (permitted TSD facilities) → facilities

Active cleanup sites (status="Active", "Inactive - Action Required") are
treated as violations because they represent acknowledged contamination
events under DTSC oversight — the same pattern used by CA GeoTracker for
open LUST cases.

This source only covers California (state="CA").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ca_dtsc_mapper import (
    is_violation_record,
    map_cleanup_site,
    map_cleanup_violation,
    map_hazwaste_site,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints (EnviroStor Public Data Export)
_SERVICE_BASE = (
    "https://services3.arcgis.com/Oy2JTCD10wkoelxS/arcgis/rest/services"
    "/Envirostor_Public_Data_Export/FeatureServer"
)
_CLEANUP_URL = f"{_SERVICE_BASE}/0/query"
_HAZWASTE_URL = f"{_SERVICE_BASE}/1/query"

class CADTSCSource(ArcGISSource):
    """Connector for CA DTSC ArcGIS REST services."""

    name = "ca_dtsc"

    def __init__(self) -> None:
        super().__init__()
        self._cleanup_attrs: list[dict] | None = None

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from CA DTSC Cleanup Sites and Hazardous Waste Sites.

        CA DTSC only covers California. Returns empty for non-CA states.
        """
        if state.upper() != "CA":
            logger.warning("Skipping %s (CA DTSC is California-only)", state)
            return

        seen_ids: set[str] = set()

        # Cleanup Sites
        cleanup_attrs = self._query_features(_CLEANUP_URL, "EnviroStor Cleanup Sites")
        self._cleanup_attrs = cleanup_attrs
        for feat in cleanup_attrs:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_cleanup_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping cleanup site: %s", e)

        cleanup_count = len(seen_ids)
        logger.info("Unique cleanup sites: %s", cleanup_count)

        # Hazardous Waste Sites
        hazwaste_attrs = self._query_features(_HAZWASTE_URL, "Hazardous Waste Sites")
        for feat in hazwaste_attrs:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_hazwaste_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping hazwaste site: %s", e)

        hazwaste_count = len(seen_ids) - cleanup_count
        logger.info("Unique hazardous waste sites: %s", hazwaste_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from active CA DTSC cleanup sites.

        Active cleanup sites (status="Active", "Inactive - Action Required",
        etc.) represent acknowledged contamination events under DTSC oversight.
        These are treated as violations, consistent with how CA GeoTracker
        handles open LUST cases.

        Only cleanup sites generate violations; hazardous waste sites are
        permitted facilities and do not have cleanup-type enforcement status.
        """
        if state.upper() != "CA":
            logger.warning("Skipping %s (CA DTSC is California-only)", state)
            return

        cleanup_attrs = self._cleanup_attrs
        if cleanup_attrs is None:
            cleanup_attrs = self._query_features(_CLEANUP_URL, "EnviroStor Cleanup Sites")
            self._cleanup_attrs = cleanup_attrs

        vio_count = 0
        for feat in cleanup_attrs:
            attrs = feat.get("attributes", feat)
            try:
                if not is_violation_record(attrs):
                    continue
                violation = map_cleanup_violation(attrs)
                if violation.source_id:
                    yield violation
                    vio_count += 1
            except Exception as e:
                logger.warning("Skipping cleanup violation: %s", e)

        logger.info("Total active-site violations: %s", vio_count)

