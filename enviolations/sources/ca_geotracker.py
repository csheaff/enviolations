"""Connector for CA GeoTracker (State Water Resources Control Board).

Downloads GeoTracker site data from data.ca.gov CKAN API:
  - GeoTracker Sites (77K records) → facilities + violations

GeoTracker is THE database CA environmental consultants use for LUST
(Leaking Underground Storage Tank) sites and groundwater contamination.
It tracks cleanup cases for LUST sites, military cleanup sites, UST sites,
land disposal sites, and other groundwater contamination cases statewide.

Data source: data.ca.gov CKAN resource dc042197-e538-4a8b-9266-9c288aa72dcd
(Ground Water - Water Quality Regulatory Information package)

This source only covers California (state="CA").
"""

from __future__ import annotations

import time
from typing import Iterator
import logging

import httpx

from ..models import Facility, Violation
from ..normalize.ca_geotracker_mapper import (
    is_violation_record,
    map_site_facility,
    map_site_violation,
)
from .base import DataSource

logger = logging.getLogger(__name__)

_CKAN_BASE = "https://data.ca.gov/api/3/action/datastore_search"

# GeoTracker Sites resource on data.ca.gov
_GEOTRACKER_SITES_RID = "dc042197-e538-4a8b-9266-9c288aa72dcd"

_PAGE_SIZE = 5000
_RATE_LIMIT_DELAY = 0.5

class CAGeoTrackerSource(DataSource):
    """Connector for CA GeoTracker via data.ca.gov CKAN API."""

    name = "ca_geotracker"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=300.0)
        self._last_request = 0.0
        self._site_records: list[dict] | None = None

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < _RATE_LIMIT_DELAY:
            time.sleep(_RATE_LIMIT_DELAY - elapsed)
        self._last_request = time.monotonic()

    def _fetch_sites(self) -> list[dict]:
        """Fetch all GeoTracker site records from data.ca.gov CKAN with pagination."""
        all_records: list[dict] = []
        offset = 0

        while True:
            self._rate_limit()
            params = {
                "resource_id": _GEOTRACKER_SITES_RID,
                "limit": str(_PAGE_SIZE),
                "offset": str(offset),
            }

            for attempt in range(3):
                try:
                    logger.info("Fetching GeoTracker sites (offset=%s)...", offset)
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

        logger.info("Total GeoTracker site records: %s", len(all_records))
        return all_records

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from GeoTracker site records.

        CA GeoTracker only covers California. Returns empty for non-CA states.
        """
        if state.upper() != "CA":
            logger.warning("Skipping %s (CA GeoTracker is California-only)", state)
            return

        records = self._fetch_sites()
        self._site_records = records

        seen_ids: set[str] = set()
        for rec in records:
            try:
                facility = map_site_facility(rec)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping site facility: %s", e)

        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from open GeoTracker cleanup cases.

        Only open/active enforcement cases generate violation records.
        Closed or informational cases are skipped.
        """
        if state.upper() != "CA":
            logger.warning("Skipping %s (CA GeoTracker is California-only)", state)
            return

        records = self._site_records
        if records is None:
            records = self._fetch_sites()
            self._site_records = records

        vio_count = 0
        for rec in records:
            try:
                if not is_violation_record(rec):
                    continue
                violation = map_site_violation(rec)
                if violation.source_id:
                    yield violation
                    vio_count += 1
            except Exception as e:
                logger.warning("Skipping site violation: %s", e)

        logger.info("Total open-case violations: %s", vio_count)

    def close(self) -> None:
        self._client.close()
