"""Connector for NY DEC (New York Department of Environmental Conservation).

Fetches JSON data from data.ny.gov via the Socrata SODA API, plus
enforcement data from the NYS GIS ArcGIS FeatureServer. Four datasets:
  - Environmental Remediation Sites → facilities (Socrata)
  - Solid Waste Management Facilities → facilities (Socrata)
  - Orders on Consent → facilities + violations (ArcGIS, 1.1K consent orders)
  - Spill Incidents → violations (Socrata, not ingested)

This source only covers New York (state="NY").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation  # noqa: F401 (Violation needed for return type)
from ..normalize.ny_dec_mapper import (
    map_remediation_site, map_solid_waste_facility,
    map_consent_order_facility, map_consent_order_violation,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# Socrata SODA API endpoint
_SODA_BASE = "https://data.ny.gov/resource/{dataset_id}.json"

# Dataset IDs
_REMEDIATION_DATASET = "c6ci-rzpg"   # Environmental Remediation Sites (~234K)
_SOLID_WASTE_DATASET = "2fni-raj8"   # Solid Waste Management Facilities (~2.7K)
# Spill Incidents dataset (u44d-k5fk, ~566K records) deliberately not ingested —
# no facility IDs or lat/lon, so spills can't be linked to real facilities.

# Socrata pagination
_PAGE_SIZE = 50000

# ArcGIS FeatureServer for consent orders
_CONSENT_ORDERS_URL = (
    "https://services6.arcgis.com/DZHaqZm9cxOD4CWM/ArcGIS"
    "/rest/services/Orders_of_Consent/FeatureServer/1/query"
)

class NYDECSource(ArcGISSource):
    """Connector for NY DEC data from data.ny.gov via SODA API."""

    name = "ny_dec"
    page_size = 1000

    def _fetch_json(self, dataset_id: str, label: str) -> list[dict]:
        """Fetch a Socrata dataset via SODA JSON API with pagination."""
        url = _SODA_BASE.format(dataset_id=dataset_id)
        return self._query_socrata(url, label, page_size=_PAGE_SIZE, order=":id")

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from NY DEC Remediation Sites and Solid Waste datasets.

        NY DEC only covers New York. Returns empty for non-NY states.
        """
        if state.upper() != "NY":
            logger.warning("Skipping %s (NY DEC is New York-only)", state)
            return

        seen_ids: set[str] = set()

        # Environmental Remediation Sites
        rows = self._fetch_json(_REMEDIATION_DATASET, "Environmental Remediation Sites")
        for row in rows:
            try:
                facility = map_remediation_site(row)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping remediation site: %s", e)

        rem_count = len(seen_ids)
        logger.info("Unique remediation sites: %s", rem_count)

        # Solid Waste Management Facilities
        rows = self._fetch_json(_SOLID_WASTE_DATASET, "Solid Waste Management Facilities")
        for row in rows:
            try:
                facility = map_solid_waste_facility(row)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping solid waste facility: %s", e)

        sw_count = len(seen_ids) - rem_count
        logger.info("Unique solid waste facilities: %s", sw_count)

        # Orders on Consent (ArcGIS FeatureServer)
        prev_count = len(seen_ids)
        try:
            consent_features = self._query_features(
                _CONSENT_ORDERS_URL, "Orders on Consent"
            )
        except Exception as e:
            logger.error("Failed to fetch consent orders: %s", e)
            consent_features = []
        self._consent_features = consent_features  # cache for fetch_violations
        for feat in consent_features:
            try:
                facility = map_consent_order_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping consent order facility: %s", e)

        oc_count = len(seen_ids) - prev_count
        logger.info("Unique consent order facilities: %s", oc_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from Orders on Consent.

        The NY DEC Spill Incidents dataset (u44d-k5fk, ~400K records) is not
        ingested — no facility IDs or lat/lon, so spills can't be linked.
        """
        if state.upper() != "NY":
            return

        # Orders on Consent — use cached features if available
        consent_features = getattr(self, "_consent_features", None)
        if consent_features is None:
            try:
                consent_features = self._query_features(
                    _CONSENT_ORDERS_URL, "Orders on Consent"
                )
            except Exception as e:
                logger.error("Failed to fetch consent orders: %s", e)
                consent_features = []

        oc_count = 0
        for feat in consent_features:
            try:
                violation = map_consent_order_violation(feat)
                if violation.source_id:
                    yield violation
                    oc_count += 1
            except Exception as e:
                logger.warning("Skipping consent order violation: %s", e)

        logger.info("Consent order violations: %s", oc_count)

