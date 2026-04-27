"""Connector for IL EPA (Illinois Environmental Protection Agency).

Downloads JSON data from IL EPA's ArcGIS REST services at
geoservices.epa.illinois.gov and Socrata open data at data.illinois.gov.
Facility datasets:
  - Federal Facilities Unit Sites → facilities
  - Illinois Landfills (Active + Post-Closure) → facilities
Violation datasets:
  - LUST Incidents (Socrata eucw-j9dg) → facilities + violations (26K)
  - OER Incidents (ArcGIS EouIncidentTracker) → facilities + violations (2.4K)

This source only covers Illinois (state="IL").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.il_epa_mapper import (
    map_federal_facility, map_landfill,
    map_lust_facility, map_lust_violation,
    map_oer_facility, map_oer_violation,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints
_FEDERAL_FACILITIES_URL = (
    "https://geoservices.epa.illinois.gov/arcgis/rest/services"
    "/Environmental/FederalFacilitiesUnitSites/MapServer/1/query"
)
_LANDFILLS_ACTIVE_URL = (
    "https://geoservices.epa.illinois.gov/arcgis/rest/services"
    "/Environmental/IllinoisLandfills/MapServer/0/query"
)
_LANDFILLS_POSTCLOSURE_URL = (
    "https://geoservices.epa.illinois.gov/arcgis/rest/services"
    "/Environmental/IllinoisLandfills/MapServer/1/query"
)
_OER_INCIDENTS_URL = (
    "https://geoservices.epa.illinois.gov/arcgis/rest/services"
    "/OER/EouIncidentTracker_DD/MapServer/2/query"
)

# Socrata SODA API
_LUST_SOCRATA_URL = "https://data.illinois.gov/resource/eucw-j9dg.json"
_SOCRATA_PAGE_SIZE = 5000

class ILEPASource(ArcGISSource):
    """Connector for IL EPA ArcGIS REST services."""

    name = "il_epa"
    page_size = 1000

    def __init__(self) -> None:
        super().__init__()
        self._lust_records: list[dict] | None = None
        self._oer_features: list[dict] | None = None

    def _fetch_socrata(self, url: str, label: str) -> list[dict]:
        """Fetch all records from a Socrata SODA JSON endpoint with pagination."""
        return self._query_socrata(url, label, page_size=_SOCRATA_PAGE_SIZE)

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from IL EPA Federal Facilities, Landfills, LUST, and OER.

        IL EPA only covers Illinois. Returns empty for non-IL states.
        """
        if state.upper() != "IL":
            logger.warning("Skipping %s (IL EPA is Illinois-only)", state)
            return

        seen_ids: set[str] = set()

        # Federal Facilities Unit Sites
        fed_features = self._query_features(_FEDERAL_FACILITIES_URL, "Federal Facilities")
        for feat in fed_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_federal_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping federal facility: %s", e)

        fed_count = len(seen_ids)
        logger.info("Unique federal facilities: %s", fed_count)

        # Active Landfills
        active_features = self._query_features(_LANDFILLS_ACTIVE_URL, "Active Landfills")
        for feat in active_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_landfill(attrs, "Active")
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping active landfill: %s", e)

        active_count = len(seen_ids) - fed_count
        logger.info("Unique active landfills: %s", active_count)

        # Post-Closure Landfills
        post_features = self._query_features(_LANDFILLS_POSTCLOSURE_URL, "Post-Closure Landfills")
        for feat in post_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_landfill(attrs, "Post-Closure")
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping post-closure landfill: %s", e)

        post_count = len(seen_ids) - fed_count - active_count
        logger.info("Unique post-closure landfills: %s", post_count)

        # LUST Incidents (Socrata)
        prev_count = len(seen_ids)
        lust_records = self._fetch_socrata(_LUST_SOCRATA_URL, "LUST Incidents")
        self._lust_records = lust_records  # cache for fetch_violations
        for rec in lust_records:
            try:
                facility = map_lust_facility(rec)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping LUST facility: %s", e)

        lust_count = len(seen_ids) - prev_count
        logger.info("Unique LUST facilities: %s", lust_count)

        # OER Incidents (ArcGIS)
        prev_count = len(seen_ids)
        oer_features = self._query_features(_OER_INCIDENTS_URL, "OER Incidents")
        self._oer_features = oer_features  # cache for fetch_violations
        for feat in oer_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_oer_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping OER facility: %s", e)

        oer_count = len(seen_ids) - prev_count
        logger.info("Unique OER facilities: %s", oer_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from IL EPA LUST Incidents and OER Incidents."""
        if state.upper() != "IL":
            return

        # LUST Incidents
        lust_records = self._lust_records
        if lust_records is None:
            lust_records = self._fetch_socrata(_LUST_SOCRATA_URL, "LUST Incidents")

        lust_count = 0
        for rec in lust_records:
            try:
                violation = map_lust_violation(rec)
                if violation.source_id:
                    yield violation
                    lust_count += 1
            except Exception as e:
                logger.warning("Skipping LUST violation: %s", e)

        logger.info("LUST violations: %s", lust_count)

        # OER Incidents
        oer_features = self._oer_features
        if oer_features is None:
            oer_features = self._query_features(_OER_INCIDENTS_URL, "OER Incidents")

        oer_count = 0
        for feat in oer_features:
            attrs = feat.get("attributes", feat)
            try:
                violation = map_oer_violation(attrs)
                if violation.source_id:
                    yield violation
                    oer_count += 1
            except Exception as e:
                logger.warning("Skipping OER violation: %s", e)

        logger.info("OER violations: %s", oer_count)
        logger.info("Total violations: %s", lust_count + oer_count)

