"""Connector for LA DEQ (Louisiana Department of Environmental Quality) data.

Downloads JSON data from LDEQ's ArcGIS Online FeatureServer at
services1.arcgis.com. Three facility datasets:
  - Water_Outfalls (7,110 outfall records, deduped by MASTER_AI_ID)
  - LDEQ_Brownfield_Sites (107 brownfield cleanup sites)
  - LDEQ_Debris_Management_Sites (413 debris/waste management sites)

Also downloads monthly enforcement action Excel files from:
  https://www.deq.louisiana.gov/page/enforcement-actions
Each file covers one month and contains enforcement orders (NOVs, COs, PAs,
etc.) keyed by AI ID which links back to la_deq facility records.

This source only covers Louisiana (state="LA").
"""

from __future__ import annotations

import io
import re
import time
from datetime import datetime
from typing import Iterator
import logging

import httpx
import openpyxl

from ..models import Facility, Violation
from ..normalize.la_deq_mapper import (
    map_brownfield_site,
    map_debris_site,
    map_enforcement_facility,
    map_enforcement_violation,
    map_water_outfall,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS Online FeatureServer base
_AGOL_BASE = (
    "https://services1.arcgis.com/9rICeNyq0Isx42vD"
    "/ArcGIS/rest/services"
)
_OUTFALLS_URL = f"{_AGOL_BASE}/Water_Outfalls_SI_20190808_wwtp/FeatureServer/0/query"
_BROWNFIELD_URL = f"{_AGOL_BASE}/LDEQ_Brownfield_Sites/FeatureServer/0/query"
_DEBRIS_URL = f"{_AGOL_BASE}/LDEQ_Debris_Management_Sites/FeatureServer/0/query"

# LDEQ enforcement actions page — monthly Excel files
_ENFORCEMENT_PAGE_URL = "https://www.deq.louisiana.gov/page/enforcement-actions"
_ENFORCEMENT_BASE_URL = "https://www.deq.louisiana.gov"

# Earliest year to collect (enforcement Excel files go back to ~2018)
_ENFORCEMENT_START_YEAR = 2018

# Column name normalisation map. Keys are lowercase versions of known header
# variants; values are the normalised key used by map_enforcement_violation().
_ENFORCEMENT_COL_MAP: dict[str, str] = {
    # Parish / county
    "parish desc": "parish",
    "parish": "parish",
    # Enforcement action number
    "enf action no": "action_no",
    "enf. action no.": "action_no",
    "enforcement action number": "action_no",
    # Agency Interest ID
    "ai id": "ai_id",
    "ai no.": "ai_id",
    "ai number": "ai_id",
    # Facility name (not used in Violation but useful for debugging)
    "ai name": "ai_name",
    "master_ai_name": "ai_name",
    # Respondent / responsible entity
    "resp entity name enforcement": "respondent",
    "respondent": "respondent",
    # Action type code
    "enf type cd": "action_type",
    "action type": "action_type",
    "action type desc": "action_type",
    # Issue date
    "issued date": "issued_date",
    "issue date": "issued_date",
    # Penalty amount
    "penalty amount": "penalty_amount",
    "penalty amt": "penalty_amount",
    "penalty amt. ": "penalty_amount",
}

class LADEQSource(ArcGISSource):
    """Connector for Louisiana DEQ ArcGIS Online FeatureServer."""

    name = "la_deq"

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from LA DEQ Water Outfalls, Brownfield, and Debris datasets.

        LA DEQ only covers Louisiana. Returns empty for non-LA states.
        """
        if state.upper() != "LA":
            logger.warning("Skipping %s (LA DEQ is Louisiana-only)", state)
            return

        seen_ids: set[str] = set()

        # Water Outfalls (primary dataset, dedup by MASTER_AI_ID)
        outfall_features = self._query_features(
            _OUTFALLS_URL, "Water Outfalls", return_geometry=True
        )
        for feat in outfall_features:
            try:
                facility = map_water_outfall(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping water outfall: %s", e)

        outfall_count = len(seen_ids)
        logger.info("Unique water outfall facilities: %s", outfall_count)

        # Brownfield Sites
        brownfield_features = self._query_features(_BROWNFIELD_URL, "Brownfield Sites")
        for feat in brownfield_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_brownfield_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping brownfield site: %s", e)

        brownfield_count = len(seen_ids) - outfall_count
        logger.info("Unique brownfield sites: %s", brownfield_count)

        # Debris Management Sites
        debris_features = self._query_features(_DEBRIS_URL, "Debris Management Sites")
        for feat in debris_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_debris_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping debris site: %s", e)

        debris_count = len(seen_ids) - outfall_count - brownfield_count
        logger.info("Unique debris management sites: %s", debris_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

        # Yield stub facilities for enforcement AI IDs not covered by ArcGIS datasets.
        # Enforcement actions span all LDEQ programs (air, solid waste, underground
        # tanks, hazardous waste, radiation, etc.) but the ArcGIS layers above only
        # cover water outfalls, brownfields, and debris sites. Without stubs, ~74% of
        # violations are orphaned — no matching facility record.
        page_urls = self._discover_enforcement_urls()
        generated_urls = self._build_enforcement_urls()
        seen_enf_urls: set[str] = set(page_urls)
        all_enf_urls = list(page_urls)
        for url in generated_urls:
            if url not in seen_enf_urls:
                seen_enf_urls.add(url)
                all_enf_urls.append(url)

        stub_count = 0
        for url in all_enf_urls:
            rows = self._fetch_enforcement_excel(url)
            for row_dict in rows:
                try:
                    stub = map_enforcement_facility(row_dict)
                    if stub and stub.source_id and stub.source_id not in seen_ids:
                        seen_ids.add(stub.source_id)
                        stub_count += 1
                        yield stub
                except Exception as e:
                    logger.warning("Skipping enforcement stub: %s", e)
        logger.info("Enforcement stub facilities added: %s", stub_count)
        logger.info("Grand total unique facilities: %s", len(seen_ids))

    def _discover_enforcement_urls(self) -> list[str]:
        """Scrape enforcement action Excel file URLs from the LDEQ website."""
        self._rate_limit()
        try:
            resp = self._client.get(_ENFORCEMENT_PAGE_URL, follow_redirects=True)
            resp.raise_for_status()
        except Exception as e:
            logger.info("Could not fetch enforcement page: %s", e)
            return []

        html = resp.text
        # Find all .xlsx links under /assets/docs/About_LDEQ/Enforcement_Actions/
        found = re.findall(
            r'/assets/docs/About_LDEQ/Enforcement_Actions/\d{4}/[^"\']+\.xlsx',
            html,
        )
        urls = [f"{_ENFORCEMENT_BASE_URL}{path}" for path in dict.fromkeys(found)]
        logger.info("Found %s enforcement Excel URLs on the page", len(urls))
        return urls

    def _build_enforcement_urls(self) -> list[str]:
        """Build enforcement Excel URLs for years from _ENFORCEMENT_START_YEAR to now."""
        current_year = datetime.now().year
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        urls = []
        for year in range(_ENFORCEMENT_START_YEAR, current_year + 1):
            for month in months:
                urls.append(
                    f"{_ENFORCEMENT_BASE_URL}/assets/docs/About_LDEQ"
                    f"/Enforcement_Actions/{year}/{month}{year}.xlsx"
                )
        return urls

    def _fetch_enforcement_excel(self, url: str) -> list[dict]:
        """Download one enforcement Excel file and return normalised row dicts.

        Returns an empty list if the file is not found or cannot be parsed.
        """
        self._rate_limit()
        for attempt in range(3):
            try:
                resp = self._client.get(url, follow_redirects=True)
                if resp.status_code == 404:
                    return []
                if resp.status_code >= 500 and attempt < 2:
                    wait = 15 * (attempt + 1)
                    logger.error("Server error %s for %s, retrying in %ss...", resp.status_code, url, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            except httpx.TimeoutException:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    logger.info("Timeout for %s, retrying in %ss...", url, wait)
                    time.sleep(wait)
                else:
                    raise
        else:
            return []

        content_type = resp.headers.get("content-type", "")
        if "html" in content_type.lower():
            # Some missing months redirect to an HTML 404 page
            return []

        try:
            wb = openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            wb.close()
        except Exception as e:
            logger.info("Could not parse %s: %s", url, e)
            return []

        if not rows:
            return []

        # Normalise headers
        raw_headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        norm_headers = [_ENFORCEMENT_COL_MAP.get(h.lower(), h.lower()) for h in raw_headers]

        result = []
        for row in rows[1:]:
            if all(v is None for v in row):
                continue
            row_dict = dict(zip(norm_headers, row))
            result.append(row_dict)
        return result

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield enforcement violations from LDEQ monthly Excel files.

        LDEQ publishes monthly enforcement action spreadsheets at:
          https://www.deq.louisiana.gov/page/enforcement-actions

        Each row is an enforcement order (NOV, CO, PA, etc.) linked to an
        LDEQ facility by AI ID (= la_deq source_id).
        """
        if state.upper() != "LA":
            logger.warning("Skipping %s (LA DEQ is Louisiana-only)", state)
            return

        # Discover URLs from the page first, then supplement with generated URLs
        # for years not shown on the page.
        page_urls = self._discover_enforcement_urls()
        generated_urls = self._build_enforcement_urls()

        # Combine: page URLs take priority; supplement with generated ones
        seen_urls: set[str] = set(page_urls)
        all_urls = list(page_urls)
        for url in generated_urls:
            if url not in seen_urls:
                seen_urls.add(url)
                all_urls.append(url)

        logger.info("Fetching enforcement violations from %s potential Excel URLs...", len(all_urls))

        total = 0
        files_with_data = 0
        for url in all_urls:
            rows = self._fetch_enforcement_excel(url)
            if not rows:
                continue
            files_with_data += 1
            file_count = 0
            for row_dict in rows:
                try:
                    violation = map_enforcement_violation(row_dict)
                    if violation.source_id:
                        yield violation
                        file_count += 1
                        total += 1
                except Exception as e:
                    logger.warning("Skipping enforcement row: %s", e)
            if file_count:
                logger.info("%s: %s violations", url.split('/')[-1], file_count)

        logger.info("Total enforcement violations: %s from %s files", total, files_with_data)

