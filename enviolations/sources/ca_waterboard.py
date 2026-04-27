"""Connector for CA State Water Board (CIWQS + SMARTS).

Downloads data from California's CKAN API at data.ca.gov:
  - CIWQS wastewater violations (322K) → violations + facility stubs
  - CIWQS wastewater enforcement (52K) → facilities + violations
  - SMARTS stormwater violations (84K) → facilities + violations

CIWQS = California Integrated Water Quality System
SMARTS = Stormwater Multiple Application & Report Tracking System

This source only covers California (state="CA").
"""

from __future__ import annotations

import time
from typing import Iterator
import logging

import httpx

from ..models import Facility, Violation
from ..normalize.ca_waterboard_mapper import (
    has_ciwqs_violation,
    map_ciwqs_enforcement_facility,
    map_ciwqs_enforcement_violation,
    map_ciwqs_violation,
    map_ciwqs_violation_facility,
    map_smarts_facility,
    map_smarts_violation,
)
from .base import DataSource

logger = logging.getLogger(__name__)

_CKAN_BASE = "https://data.ca.gov/api/3/action/datastore_search"

# Resource IDs on data.ca.gov
_CIWQS_VIOLATIONS_RID = "e397598d-6a92-4769-a135-76fa000cb5c7"
_CIWQS_ENFORCEMENT_RID = "64f25cad-2e10-4a66-8368-79293f56c2f1"
_SMARTS_VIOLATIONS_RID = "9b69a654-0c9a-4865-8d10-38c55b1b8c58"

_PAGE_SIZE = 5000
_RATE_LIMIT_DELAY = 0.5

class CAWaterBoardSource(DataSource):
    """Connector for CA State Water Board CKAN API."""

    name = "ca_waterboard"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=300.0)
        self._last_request = 0.0
        self._ciwqs_enf_records: list[dict] | None = None
        self._ciwqs_vio_records: list[dict] | None = None
        self._smarts_vio_records: list[dict] | None = None

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < _RATE_LIMIT_DELAY:
            time.sleep(_RATE_LIMIT_DELAY - elapsed)
        self._last_request = time.monotonic()

    def _fetch_ckan(self, resource_id: str, label: str) -> list[dict]:
        """Fetch all records from a CKAN datastore resource with pagination."""
        all_records: list[dict] = []
        offset = 0

        while True:
            self._rate_limit()
            params = {
                "resource_id": resource_id,
                "limit": str(_PAGE_SIZE),
                "offset": str(offset),
            }

            for attempt in range(3):
                try:
                    logger.info("Fetching %s (offset=%s)...", label, offset)
                    resp = self._client.get(_CKAN_BASE, params=params, follow_redirects=True)

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
                        logger.info("Timeout, retrying in %ss...", wait)
                        time.sleep(wait)
                    else:
                        raise
            else:
                break

            data = resp.json()
            if not data.get("success"):
                err = data.get("error", {})
                logger.error("CKAN error: %s", err)
                break

            records = data.get("result", {}).get("records", [])
            all_records.extend(records)
            page_count = len(records)
            logger.info("Got %s records (total so far: %s)", page_count, len(all_records))

            if page_count < _PAGE_SIZE:
                break

            offset += _PAGE_SIZE

        logger.info("Total %s records: %s", label, len(all_records))
        return all_records

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from CIWQS enforcement + SMARTS violations.

        CA Water Board only covers California. Returns empty for non-CA states.
        """
        if state.upper() != "CA":
            logger.warning("Skipping %s (CA Water Board is California-only)", state)
            return

        seen_ids: set[str] = set()

        # CIWQS enforcement records have full facility details
        enf_records = self._fetch_ckan(_CIWQS_ENFORCEMENT_RID, "CIWQS Enforcement")
        self._ciwqs_enf_records = enf_records
        for rec in enf_records:
            try:
                facility = map_ciwqs_enforcement_facility(rec)
                if facility and facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping CIWQS enforcement facility: %s", e)

        enf_count = len(seen_ids)
        logger.info("Unique CIWQS enforcement facilities: %s", enf_count)

        # SMARTS violations have embedded facility details (PLACE_* fields)
        smarts_records = self._fetch_ckan(_SMARTS_VIOLATIONS_RID, "SMARTS Violations")
        self._smarts_vio_records = smarts_records
        for rec in smarts_records:
            try:
                facility = map_smarts_facility(rec)
                if facility and facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping SMARTS facility: %s", e)

        smarts_count = len(seen_ids) - enf_count
        logger.info("Unique SMARTS facilities: %s", smarts_count)

        # CIWQS violations reference facilities that may not appear in the enforcement
        # dataset (facilities with violations but no enforcement actions). Emit stub
        # facility records for any FACILITY_ID not already covered by enforcement.
        ciwqs_records = self._fetch_ckan(_CIWQS_VIOLATIONS_RID, "CIWQS Violations")
        self._ciwqs_vio_records = ciwqs_records
        stub_count = 0
        for rec in ciwqs_records:
            try:
                if not has_ciwqs_violation(rec):
                    continue
                facility = map_ciwqs_violation_facility(rec)
                if facility and facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    stub_count += 1
            except Exception as e:
                logger.warning("Skipping CIWQS violation facility stub: %s", e)

        logger.info("CIWQS violation facility stubs (new): %s", stub_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from CIWQS + SMARTS."""
        if state.upper() != "CA":
            return

        # CIWQS wastewater violations (322K)
        ciwqs_records = self._ciwqs_vio_records
        if ciwqs_records is None:
            ciwqs_records = self._fetch_ckan(_CIWQS_VIOLATIONS_RID, "CIWQS Violations")
        ciwqs_count = 0
        for rec in ciwqs_records:
            try:
                if not has_ciwqs_violation(rec):
                    continue
                violation = map_ciwqs_violation(rec)
                if violation.source_id:
                    yield violation
                    ciwqs_count += 1
            except Exception as e:
                logger.warning("Skipping CIWQS violation: %s", e)

        logger.info("CIWQS violations: %s", ciwqs_count)

        # CIWQS enforcement actions as violations (52K)
        enf_records = self._ciwqs_enf_records
        if enf_records is None:
            enf_records = self._fetch_ckan(_CIWQS_ENFORCEMENT_RID, "CIWQS Enforcement")

        enf_count = 0
        for rec in enf_records:
            try:
                violation = map_ciwqs_enforcement_violation(rec)
                if violation and violation.source_id:
                    yield violation
                    enf_count += 1
            except Exception as e:
                logger.warning("Skipping CIWQS enforcement violation: %s", e)

        logger.info("CIWQS enforcement violations: %s", enf_count)

        # SMARTS stormwater violations (84K)
        smarts_records = self._smarts_vio_records
        if smarts_records is None:
            smarts_records = self._fetch_ckan(_SMARTS_VIOLATIONS_RID, "SMARTS Violations")

        smarts_count = 0
        for rec in smarts_records:
            try:
                violation = map_smarts_violation(rec)
                if violation.source_id:
                    yield violation
                    smarts_count += 1
            except Exception as e:
                logger.warning("Skipping SMARTS violation: %s", e)

        logger.info("SMARTS violations: %s", smarts_count)
        logger.info("Total violations: %s", ciwqs_count + enf_count + smarts_count)

    def close(self) -> None:
        self._client.close()
