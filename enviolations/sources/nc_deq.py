"""Connector for NC DEQ (North Carolina Dept. of Environmental Quality).

Downloads JSON data from NC DEQ's ArcGIS Online FeatureServer. Four datasets:
  - HW_Sites/FeatureServer/0: Hazardous Waste Sites → facilities
  - Underground_Storage_Tank_Incidents/FeatureServer/0 → facilities + violations (45K)
  - AST_Incidents/FeatureServer/0 → facilities + violations (9K)
  - Report_an_SSO_Public_View/FeatureServer/0 → facilities + violations (3.2K)

This source only covers North Carolina (state="NC").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation  # noqa: F401 (Violation needed for return type)
from ..normalize.nc_deq_mapper import (
    map_hw_site,
    map_tank_incident_facility, map_tank_incident_violation,
    map_sso_facility, map_sso_violation,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS Online FeatureServer endpoints (all under NC DEQ org kCu40SDxsCGcuUWO)
_HW_SITES_URL = (
    "https://services2.arcgis.com/kCu40SDxsCGcuUWO/arcgis/rest/services"
    "/HW_Sites/FeatureServer/0/query"
)
_UST_INCIDENTS_URL = (
    "https://services2.arcgis.com/kCu40SDxsCGcuUWO/arcgis/rest/services"
    "/Underground_Storage_Tank_Incidents/FeatureServer/0/query"
)
_AST_INCIDENTS_URL = (
    "https://services2.arcgis.com/kCu40SDxsCGcuUWO/arcgis/rest/services"
    "/AST_Incidents/FeatureServer/0/query"
)
_SSO_REPORTS_URL = (
    "https://services2.arcgis.com/kCu40SDxsCGcuUWO/arcgis/rest/services"
    "/Report_an_SSO_Public_View/FeatureServer/0/query"
)

class NCDEQSource(ArcGISSource):
    """Connector for NC DEQ ArcGIS Online FeatureServer."""

    name = "nc_deq"

    def __init__(self) -> None:
        super().__init__()
        self._ust_features: list[dict] | None = None
        self._ast_features: list[dict] | None = None
        self._sso_features: list[dict] | None = None

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from NC DEQ HW Sites, UST/AST Incidents, and SSO Reports.

        NC DEQ only covers North Carolina. Returns empty for non-NC states.
        """
        if state.upper() != "NC":
            logger.warning("Skipping %s (NC DEQ is North Carolina-only)", state)
            return

        seen_ids: set[str] = set()

        # Hazardous Waste Sites
        hw_features = self._query_features(_HW_SITES_URL, "Hazardous Waste Sites")
        for feat in hw_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_hw_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping HW site: %s", e)

        hw_count = len(seen_ids)
        logger.info("Unique HW sites: %s", hw_count)

        # UST Incidents
        prev_count = len(seen_ids)
        ust_features = self._query_features(_UST_INCIDENTS_URL, "UST Incidents")
        self._ust_features = ust_features  # cache for fetch_violations
        for feat in ust_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_tank_incident_facility(attrs, "ust")
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST incident facility: %s", e)

        ust_count = len(seen_ids) - prev_count
        logger.info("Unique UST incident facilities: %s", ust_count)

        # AST Incidents
        prev_count = len(seen_ids)
        ast_features = self._query_features(_AST_INCIDENTS_URL, "AST Incidents")
        self._ast_features = ast_features  # cache for fetch_violations
        for feat in ast_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_tank_incident_facility(attrs, "ast")
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping AST incident facility: %s", e)

        ast_count = len(seen_ids) - prev_count
        logger.info("Unique AST incident facilities: %s", ast_count)

        # SSO Reports
        prev_count = len(seen_ids)
        sso_features = self._query_features(_SSO_REPORTS_URL, "SSO Reports")
        self._sso_features = sso_features  # cache for fetch_violations
        for feat in sso_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_sso_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping SSO facility: %s", e)

        sso_count = len(seen_ids) - prev_count
        logger.info("Unique SSO facilities: %s", sso_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from UST Incidents, AST Incidents, and SSO Reports."""
        if state.upper() != "NC":
            return

        # UST Incidents
        ust_features = self._ust_features
        if ust_features is None:
            ust_features = self._query_features(_UST_INCIDENTS_URL, "UST Incidents")

        ust_count = 0
        for feat in ust_features:
            attrs = feat.get("attributes", feat)
            try:
                violation = map_tank_incident_violation(attrs, "ust")
                if violation.source_id:
                    yield violation
                    ust_count += 1
            except Exception as e:
                logger.warning("Skipping UST violation: %s", e)

        logger.info("UST incident violations: %s", ust_count)

        # AST Incidents
        ast_features = self._ast_features
        if ast_features is None:
            ast_features = self._query_features(_AST_INCIDENTS_URL, "AST Incidents")

        ast_count = 0
        for feat in ast_features:
            attrs = feat.get("attributes", feat)
            try:
                violation = map_tank_incident_violation(attrs, "ast")
                if violation.source_id:
                    yield violation
                    ast_count += 1
            except Exception as e:
                logger.warning("Skipping AST violation: %s", e)

        logger.info("AST incident violations: %s", ast_count)

        # SSO Reports
        sso_features = self._sso_features
        if sso_features is None:
            sso_features = self._query_features(_SSO_REPORTS_URL, "SSO Reports")

        sso_count = 0
        for feat in sso_features:
            attrs = feat.get("attributes", feat)
            try:
                violation = map_sso_violation(attrs)
                if violation.source_id:
                    yield violation
                    sso_count += 1
            except Exception as e:
                logger.warning("Skipping SSO violation: %s", e)

        logger.info("SSO violations: %s", sso_count)
        logger.info("Total violations: %s", ust_count + ast_count + sso_count)

