"""Connector for AL ADEM (Alabama Department of Environmental Management) data.

Downloads JSON data from AL ADEM's ArcGIS REST services at gis.adem.alabama.gov.
Facility datasets:
  - Active UST Sites (UST_SWAA_FieldOps/MapServer/0): ~5,287 underground storage tanks
  - Landfills (Landfills2024/MapServer/0): ~169 solid waste facilities
  - Brownfields (Brownfields/FeatureServer/0): ~776 cleanup sites
Violation datasets:
  - SSO Reports (SSO_all_Dates_project/MapServer/0) → facilities + violations (8.6K)
  - UST Incidents (UST_Incidents_GCS/MapServer/0) → facilities + violations (5.4K)

Note: AL ADEM uses a self-signed SSL certificate, so verify=False is required.

This source only covers Alabama (state="AL").
"""

from __future__ import annotations

import time
from typing import Iterator
import logging

import httpx

from ..models import Facility, Violation
from ..normalize.al_adem_mapper import (
    map_brownfield,
    map_landfill,
    map_sso_facility,
    map_sso_violation,
    map_ust_incident_facility,
    map_ust_incident_violation,
    map_ust_site,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://gis.adem.alabama.gov/arcgis/rest/services"

_UST_URL = f"{_BASE}/UST_SWAA_FieldOps/MapServer/0/query"
_LANDFILL_URL = f"{_BASE}/Landfills2024/MapServer/0/query"
_BROWNFIELD_URL = f"{_BASE}/Brownfields/FeatureServer/0/query"
_SSO_URL = f"{_BASE}/SSO_all_Dates_project/MapServer/0/query"
_UST_INCIDENTS_URL = f"{_BASE}/UST_Incidents_GCS/MapServer/0/query"

class ALADEMSource(ArcGISSource):
    """Connector for Alabama ADEM ArcGIS REST services."""

    name = "al_adem"
    page_size = 1000

    def __init__(self) -> None:
        super().__init__()
        # AL ADEM uses a self-signed SSL certificate
        self._client = httpx.Client(timeout=self.timeout, verify=False)
        self._sso_attrs: list[dict] | None = None
        self._ust_incident_attrs: list[dict] | None = None

    def _query_sso_features(self, url: str) -> list[dict]:
        """Query the SSO layer using sso_report_id range pagination.

        The SSO MapServer doesn't support offset pagination or OBJECTID queries.
        We chunk by sso_report_id ranges (0-500, 500-1000, ...) to stay under
        the 1000-record server limit per request.
        """
        all_features: list[dict] = []
        chunk_size = 500
        start = 0
        max_id = 20000  # generous upper bound

        while start <= max_id:
            self._rate_limit()
            end = start + chunk_size
            where = f"sso_report_id>={start} AND sso_report_id<{end}"
            params = {
                "where": where,
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "json",
            }

            for attempt in range(3):
                try:
                    logger.info("Querying SSO Reports (id %s-%s)...", start, end)
                    resp = self._client.get(url, params=params, follow_redirects=True)

                    if resp.status_code >= 500 and attempt < 2:
                        wait = 15 * (attempt + 1)
                        logger.error("Server error %s, retrying in %ss...", resp.status_code, wait)
                        time.sleep(wait)
                        continue

                    resp.raise_for_status()
                    break
                except httpx.TimeoutException:
                    if attempt < 2:
                        wait = 15 * (attempt + 1)
                        logger.info("Timeout querying SSO Reports, retrying in %ss...", wait)
                        time.sleep(wait)
                    else:
                        raise
            else:
                start = end
                continue

            data = resp.json()

            if "error" in data:
                err = data["error"]
                logger.error("ArcGIS error: %s", err.get('message', err))
                start = end
                continue

            features = data.get("features", [])
            if features:
                all_features.extend(features)
                logger.info("Got %s features (total so far: %s)", len(features), len(all_features))

            start = end

        logger.info("Total SSO Reports features: %s", len(all_features))
        return all_features

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "AL":
            logger.warning("Skipping %s (AL ADEM is Alabama-only)", state)
            return

        seen_ids: set[str] = set()

        # Active UST Sites
        ust_features = self._query_features(_UST_URL, "Active UST Sites")
        for feat in ust_features:
            try:
                facility = map_ust_site(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST site: %s", e)

        ust_count = len(seen_ids)
        logger.info("Unique UST sites: %s", ust_count)

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

        lf_count = len(seen_ids) - ust_count
        logger.info("Unique landfills: %s", lf_count)

        # Brownfields
        bf_features = self._query_features(_BROWNFIELD_URL, "Brownfields")
        for feat in bf_features:
            try:
                facility = map_brownfield(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping brownfield: %s", e)

        bf_count = len(seen_ids) - ust_count - lf_count
        logger.info("Unique brownfields: %s", bf_count)

        # SSO Reports (ID-range pagination — server doesn't support offset)
        prev_count = len(seen_ids)
        sso_features = self._query_sso_features(_SSO_URL)
        sso_attrs = [feat.get("attributes", {}) for feat in sso_features]
        self._sso_attrs = sso_attrs  # cache for fetch_violations
        for attrs in sso_attrs:
            try:
                facility = map_sso_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping SSO facility: %s", e)

        sso_count = len(seen_ids) - prev_count
        logger.info("Unique SSO facilities: %s", sso_count)

        # UST Incidents
        prev_count = len(seen_ids)
        ust_inc_features = self._query_features(_UST_INCIDENTS_URL, "UST Incidents")
        ust_inc_attrs = [feat.get("attributes", {}) for feat in ust_inc_features]
        self._ust_incident_attrs = ust_inc_attrs  # cache for fetch_violations
        for attrs in ust_inc_attrs:
            try:
                facility = map_ust_incident_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST incident facility: %s", e)

        ust_inc_count = len(seen_ids) - prev_count
        logger.info("Unique UST incident facilities: %s", ust_inc_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "AL":
            return

        # SSO Reports
        sso_attrs = self._sso_attrs
        if sso_attrs is None:
            sso_features = self._query_sso_features(_SSO_URL)
            sso_attrs = [feat.get("attributes", {}) for feat in sso_features]

        sso_count = 0
        for attrs in sso_attrs:
            try:
                violation = map_sso_violation(attrs)
                if violation.source_id:
                    yield violation
                    sso_count += 1
            except Exception as e:
                logger.warning("Skipping SSO violation: %s", e)

        logger.info("SSO violations: %s", sso_count)

        # UST Incidents
        ust_inc_attrs = self._ust_incident_attrs
        if ust_inc_attrs is None:
            ust_inc_features = self._query_features(_UST_INCIDENTS_URL, "UST Incidents")
            ust_inc_attrs = [feat.get("attributes", {}) for feat in ust_inc_features]

        ust_inc_count = 0
        for attrs in ust_inc_attrs:
            try:
                violation = map_ust_incident_violation(attrs)
                if violation.source_id:
                    yield violation
                    ust_inc_count += 1
            except Exception as e:
                logger.warning("Skipping UST incident violation: %s", e)

        logger.info("UST incident violations: %s", ust_inc_count)
        logger.info("Total violations: %s", sso_count + ust_inc_count)

