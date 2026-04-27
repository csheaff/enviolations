"""Connector for ME DEP (Maine Department of Environmental Protection) data.

Downloads JSON data from Maine DEP's ArcGIS REST services at gis.maine.gov.
Three facility datasets:
  - Registered Tanks (all_registered_tanks/MapServer/0): ~12,458 UST/AST sites
  - Remediation Sites (Remediation_Sites/MapServer/0): ~3,940 cleanup sites
  - MEPDES Facilities (MainePollutantDischargeEliminationSystem/MapServer/0): ~500 water discharge permits

ME DEP does not publish structured violation/enforcement data via ArcGIS;
violations for Maine are covered by federal EPA ECHO.

This source only covers Maine (state="ME").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.me_dep_mapper import map_mepdes, map_remediation, map_tank
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://gis.maine.gov/mapservices/rest/services/dep"

_TANKS_URL = f"{_BASE}/all_registered_tanks/MapServer/0/query"
_REMEDIATION_URL = f"{_BASE}/Remediation_Sites/MapServer/0/query"
_MEPDES_URL = f"{_BASE}/MainePollutantDischargeEliminationSystem/MapServer/0/query"

class MEDEPSource(ArcGISSource):
    """Connector for Maine DEP ArcGIS REST services."""

    name = "me_dep"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "ME":
            logger.warning("Skipping %s (ME DEP is Maine-only)", state)
            return

        seen_ids: set[str] = set()

        # Registered Tanks (deduplicate by registration number)
        tank_features = self._query_features(_TANKS_URL, "Registered Tanks")
        tank_reg_seen: set = set()
        for feat in tank_features:
            try:
                facility = map_tank(feat)
                reg_num = feat.get("attributes", {}).get("REGISTRATION_NUMBER")
                if reg_num and reg_num in tank_reg_seen:
                    continue
                if reg_num:
                    tank_reg_seen.add(reg_num)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping tank: %s", e)

        tank_count = len(seen_ids)
        logger.info("Unique tank facilities: %s", tank_count)

        # Remediation Sites
        rem_features = self._query_features(_REMEDIATION_URL, "Remediation Sites")
        for feat in rem_features:
            try:
                facility = map_remediation(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping remediation site: %s", e)

        rem_count = len(seen_ids) - tank_count
        logger.info("Unique remediation sites: %s", rem_count)

        # MEPDES Water Discharge
        mepdes_features = self._query_features(_MEPDES_URL, "MEPDES Facilities")
        for feat in mepdes_features:
            try:
                facility = map_mepdes(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping MEPDES facility: %s", e)

        mepdes_count = len(seen_ids) - tank_count - rem_count
        logger.info("Unique MEPDES facilities: %s", mepdes_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "ME":
            logger.warning("Skipping %s (ME DEP is Maine-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Maine violations")
        return
        yield  # make this a generator

