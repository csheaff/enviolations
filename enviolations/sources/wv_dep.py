"""Connector for WV DEP (West Virginia Dept of Environmental Protection) data.

Downloads facility data from WV DEP's TAGIS ArcGIS Enterprise at
tagis.dep.wv.gov.
Facility datasets:
  - Landfills (waste_management/MapServer/0): solid waste facilities
  - Voluntary Remediation Sites (environmental_remediation/MapServer/2): cleanup sites
  - SEMS Sites (environmental_remediation/MapServer/14): Superfund/contamination sites
  - Air Quality All Facilities (air_quality/MapServer/7): air emission sources
  - Mining Permits Inspection Status (mining_reclamation/MapServer/9): mining permits
Violation datasets:
  - Mining Permits with violations (mining_reclamation/MapServer/9) → violations
    (4.4K permits, ~2.1K with active/total violations)

This source only covers West Virginia (state="WV").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.wv_dep_mapper import (
    map_air_facility,
    map_landfill,
    map_mining_facility,
    map_mining_violation,
    map_sems_site,
    map_vr_site,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://tagis.dep.wv.gov/arcgis/rest/services/WVDEP_enterprise"

_LANDFILL_URL = f"{_BASE}/waste_management/MapServer/0/query"
_VR_URL = f"{_BASE}/environmental_remediation/MapServer/2/query"
_SEMS_URL = f"{_BASE}/environmental_remediation/MapServer/14/query"
_AIR_URL = f"{_BASE}/air_quality/MapServer/7/query"
_MINING_URL = f"{_BASE}/mining_reclamation/MapServer/9/query"

class WVDEPSource(ArcGISSource):
    """Connector for West Virginia DEP ArcGIS MapServer."""

    name = "wv_dep"
    page_size = 1000

    def __init__(self) -> None:
        super().__init__()
        self._mining_features: list[dict] | None = None

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "WV":
            logger.warning("Skipping %s (WV DEP is West Virginia-only)", state)
            return

        seen_ids: set[str] = set()

        # Landfills
        lf_features = self._query_features(_LANDFILL_URL, "Landfills")
        for feat in lf_features:
            try:
                facility = map_landfill(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping landfill: %s", e)

        lf_count = len(seen_ids)
        logger.info("Unique landfills: %s", lf_count)

        # Voluntary Remediation Sites
        vr_features = self._query_features(_VR_URL, "Voluntary Remediation Sites")
        for feat in vr_features:
            try:
                facility = map_vr_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping VR site: %s", e)

        vr_count = len(seen_ids) - lf_count
        logger.info("Unique VR sites: %s", vr_count)

        # SEMS Sites
        sems_features = self._query_features(_SEMS_URL, "SEMS Sites")
        for feat in sems_features:
            try:
                facility = map_sems_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping SEMS site: %s", e)

        sems_count = len(seen_ids) - lf_count - vr_count
        logger.info("Unique SEMS sites: %s", sems_count)

        # Air Quality - All Facilities
        air_features = self._query_features(_AIR_URL, "Air Quality All Facilities")
        for feat in air_features:
            try:
                facility = map_air_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air facility: %s", e)

        air_count = len(seen_ids) - lf_count - vr_count - sems_count
        logger.info("Unique air facilities: %s", air_count)

        # Mining Permits Inspection Status
        prev_count = len(seen_ids)
        mining_features = self._query_features(_MINING_URL, "Mining Permits")
        self._mining_features = mining_features  # cache for fetch_violations
        for feat in mining_features:
            try:
                facility = map_mining_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping mining facility: %s", e)

        mining_count = len(seen_ids) - prev_count
        logger.info("Unique mining facilities: %s", mining_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "WV":
            return

        # Mining Permits with violations
        mining_features = self._mining_features
        if mining_features is None:
            mining_features = self._query_features(_MINING_URL, "Mining Permits")

        mining_count = 0
        for feat in mining_features:
            try:
                attrs = feat.get("attributes", {})
                total_vio = attrs.get("total_vio") or 0
                if total_vio > 0:
                    violation = map_mining_violation(feat)
                    if violation.source_id:
                        yield violation
                        mining_count += 1
            except Exception as e:
                logger.warning("Skipping mining violation: %s", e)

        logger.info("Mining violations: %s", mining_count)

