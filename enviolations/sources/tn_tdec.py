"""Connector for TN TDEC (Tennessee Dept of Environment and Conservation) data.

Downloads JSON data from TN TDEC's ArcGIS REST services at tdeconline.tn.gov.
Facility datasets:
  - Air Pollution Control Permits (APC_Permits/MapServer/0)
  - Active UST Facilities (UST_Facilities/MapServer/0)
  - Remediation Sites (DOR_Sites/MapServer/0)
  - Solid Waste Management Permits (SWM_Permits/MapServer/0)
Violation datasets:
  - GWP Complaints (GWP_Complaints/MapServer/0) → facilities + violations (9.2K)

This source only covers Tennessee (state="TN").
"""

from __future__ import annotations

from typing import Iterator
import logging

import httpx

from ..models import Facility, Violation
from ..normalize.tn_tdec_mapper import (
    map_apc_permit,
    map_gwp_facility,
    map_gwp_violation,
    map_remediation_site,
    map_swm_permit,
    map_ust_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API URLs
_BASE = "https://tdeconline.tn.gov/arcgis/rest/services"
_APC_URL = f"{_BASE}/APC_Permits/MapServer/0/query"
_UST_URL = f"{_BASE}/UST_Facilities/MapServer/0/query"
_DOR_URL = f"{_BASE}/DOR_Sites/MapServer/0/query"
_SWM_URL = f"{_BASE}/SWM_Permits/MapServer/0/query"
_GWP_URL = f"{_BASE}/GWP_Complaints/MapServer/0/query"

class TNTDECSource(ArcGISSource):
    """Connector for Tennessee TDEC ArcGIS REST services."""

    name = "tn_tdec"
    page_size = 1000

    def __init__(self) -> None:
        super().__init__()
        # TN TDEC ArcGIS server blocks non-browser User-Agents and requests that
        # don't include standard browser Accept/Referer headers. Send a complete
        # browser-like header set to avoid 403 responses.
        self._client = httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://tdeconline.tn.gov/",
            },
        )
        self._gwp_features: list[dict] | None = None

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from TN TDEC APC, UST, DOR, SWM, and GWP datasets.

        TN TDEC only covers Tennessee. Returns empty for non-TN states.

        Each sub-dataset is fetched independently. If one fails (e.g., the
        server blocks certain endpoints with HTTP 403), the others continue.
        An error is raised only if ALL sub-datasets fail, since that indicates
        a network-level block rather than a partial dataset issue.
        """
        if state.upper() != "TN":
            logger.warning("Skipping %s (TN TDEC is Tennessee-only)", state)
            return

        seen_ids: set[str] = set()
        failed_datasets: list[str] = []

        # Air Pollution Control Permits (multiple permits per site — dedup by SITE_ID)
        try:
            apc_features = self._query_features(_APC_URL, "APC Permits")
            for feat in apc_features:
                try:
                    facility = map_apc_permit(feat)
                    if facility.source_id and facility.source_id not in seen_ids:
                        seen_ids.add(facility.source_id)
                        yield facility
                except Exception as e:
                    logger.warning("Skipping APC permit: %s", e)
        except Exception as e:
            logger.error("APC Permits fetch failed (skipping): %s", e)
            failed_datasets.append("APC")

        apc_count = len(seen_ids)
        logger.info("Unique APC sites: %s", apc_count)

        # Active UST Facilities
        try:
            ust_features = self._query_features(_UST_URL, "UST Facilities")
            for feat in ust_features:
                try:
                    facility = map_ust_facility(feat)
                    if facility.source_id and facility.source_id not in seen_ids:
                        seen_ids.add(facility.source_id)
                        yield facility
                except Exception as e:
                    logger.warning("Skipping UST facility: %s", e)
        except Exception as e:
            logger.error("UST Facilities fetch failed (skipping): %s", e)
            failed_datasets.append("UST")

        ust_count = len(seen_ids) - apc_count
        logger.info("Unique UST facilities: %s", ust_count)

        # Remediation Sites (DOR)
        try:
            dor_features = self._query_features(_DOR_URL, "Remediation Sites")
            for feat in dor_features:
                try:
                    facility = map_remediation_site(feat)
                    if facility.source_id and facility.source_id not in seen_ids:
                        seen_ids.add(facility.source_id)
                        yield facility
                except Exception as e:
                    logger.warning("Skipping remediation site: %s", e)
        except Exception as e:
            logger.error("DOR Sites fetch failed (skipping): %s", e)
            failed_datasets.append("DOR")

        dor_count = len(seen_ids) - apc_count - ust_count
        logger.info("Unique remediation sites: %s", dor_count)

        # Solid Waste Management Permits (multiple permits per site — dedup by SITE_ID)
        try:
            swm_features = self._query_features(_SWM_URL, "SWM Permits")
            for feat in swm_features:
                try:
                    facility = map_swm_permit(feat)
                    if facility.source_id and facility.source_id not in seen_ids:
                        seen_ids.add(facility.source_id)
                        yield facility
                except Exception as e:
                    logger.warning("Skipping SWM permit: %s", e)
        except Exception as e:
            logger.error("SWM Permits fetch failed (skipping): %s", e)
            failed_datasets.append("SWM")

        swm_count = len(seen_ids) - apc_count - ust_count - dor_count
        logger.info("Unique SWM sites: %s", swm_count)

        # GWP Complaints → facilities (also populates self._gwp_features for fetch_violations)
        prev_count = len(seen_ids)
        try:
            gwp_features = self._query_features(_GWP_URL, "GWP Complaints")
            self._gwp_features = gwp_features
            for feat in gwp_features:
                try:
                    facility = map_gwp_facility(feat)
                    if facility and facility.source_id and facility.source_id not in seen_ids:
                        seen_ids.add(facility.source_id)
                        yield facility
                except Exception as e:
                    logger.warning("Skipping GWP facility: %s", e)
        except Exception as e:
            logger.error("GWP Complaints fetch failed (skipping): %s", e)
            failed_datasets.append("GWP")
            self._gwp_features = []

        gwp_count = len(seen_ids) - prev_count
        logger.info("Unique GWP facilities: %s", gwp_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

        # If every sub-dataset failed, raise so the ingest is logged as failed
        # rather than silently succeeding with 0 facilities.
        if len(failed_datasets) == 5:
            raise RuntimeError(
                f"All TN TDEC sub-datasets failed: {', '.join(failed_datasets)}. "
                "Server may be blocking requests from this IP address."
            )

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from GWP Complaints."""
        if state.upper() != "TN":
            return

        gwp_features = self._gwp_features
        if gwp_features is None:
            gwp_features = self._query_features(_GWP_URL, "GWP Complaints")

        gwp_count = 0
        for feat in gwp_features:
            try:
                violation = map_gwp_violation(feat)
                if violation and violation.source_id:
                    yield violation
                    gwp_count += 1
            except Exception as e:
                logger.warning("Skipping GWP violation: %s", e)

        logger.info("GWP violations: %s", gwp_count)
