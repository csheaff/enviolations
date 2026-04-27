"""Connector for PA DEP (Pennsylvania Department of Environmental Protection).

Downloads CSV data from the Pennsylvania Open Data Portal (data.pa.gov) via Socrata.
Three datasets:
  - EIS Facilities (air emission plants) → facilities
  - Safe Drinking Water Facilities → facilities
  - Oil & Gas Well Inspections → violations

This source only covers Pennsylvania (state="PA").
"""

from __future__ import annotations

import csv
import io
import time
from typing import Iterator
import logging

import httpx

from ..models import Facility, Violation
from ..normalize.pa_dep_mapper import (
    is_violation_result,
    map_eis_facility,
    map_inspection,
    map_sdwa_facility,
    map_well_facility,
)
from .base import DataSource

logger = logging.getLogger(__name__)

# Socrata bulk CSV download pattern
_SOCRATA_BASE = "https://data.pa.gov/api/views/{dataset_id}/rows.csv"

# Facility datasets
_EIS_DATASET = "e7ip-7qrs"    # EIS Facilities (air emissions), ~28K rows / ~3.4K unique
_SDWA_DATASET = "afhy-him4"   # Safe Drinking Water Facilities, ~47K rows / ~19.7K unique

# Violation dataset
_INSPECTION_DATASET = "f8fx-8zip"  # Oil & Gas Well Inspections

_RATE_LIMIT_DELAY = 2.0

class PADEPSource(DataSource):
    """Connector for PA DEP bulk CSV data from the Pennsylvania Open Data Portal."""

    name = "pa_dep"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=300.0)
        self._last_request = 0.0
        self._inspection_rows: list[dict] | None = None

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < _RATE_LIMIT_DELAY:
            time.sleep(_RATE_LIMIT_DELAY - elapsed)
        self._last_request = time.monotonic()

    def _download_csv(self, dataset_id: str, label: str) -> list[dict]:
        """Download a Socrata dataset as CSV rows (list of dicts)."""
        url = _SOCRATA_BASE.format(dataset_id=dataset_id)
        for attempt in range(3):
            self._rate_limit()
            try:
                logger.info("Downloading %s (%s)...", label, dataset_id)
                resp = self._client.get(
                    url,
                    params={"accessType": "DOWNLOAD"},
                    follow_redirects=True,
                )
                if resp.status_code >= 500 and attempt < 2:
                    wait = 15 * (attempt + 1)
                    logger.error("Server error %s, retrying in %ss...", resp.status_code, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                text = resp.text
                reader = csv.DictReader(io.StringIO(text))
                # Strip whitespace from column names (Socrata CSVs sometimes
                # include trailing spaces in headers, e.g. "County Centroid Latitude ")
                rows = [{k.strip(): v for k, v in row.items()} for row in reader]
                logger.info("Got %s rows from %s", len(rows), label)
                return rows
            except httpx.TimeoutException:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    logger.info("Timeout downloading %s, retrying in %ss...", label, wait)
                    time.sleep(wait)
                else:
                    raise
        return []

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from PA DEP EIS and Safe Drinking Water datasets.

        PA DEP only covers Pennsylvania. Returns empty for non-PA states.
        """
        if state.upper() != "PA":
            logger.warning("Skipping %s (PA DEP is Pennsylvania-only)", state)
            return

        seen_ids: set[str] = set()

        # EIS Facilities (air emission plants) — deduplicate by facility site ID
        rows = self._download_csv(_EIS_DATASET, "EIS Facilities")
        for row in rows:
            try:
                facility = map_eis_facility(row)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping EIS facility: %s", e)

        eis_count = len(seen_ids)
        logger.info("Unique EIS facilities: %s", eis_count)

        # Safe Drinking Water Facilities — deduplicate by PWS ID
        rows = self._download_csv(_SDWA_DATASET, "Safe Drinking Water Facilities")
        for row in rows:
            try:
                facility = map_sdwa_facility(row)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping SDWA facility: %s", e)

        sdwa_count = len(seen_ids) - eis_count
        logger.info("Unique SDWA facilities: %s", sdwa_count)

        # O&G Well stubs — each unique PERMIT gets a facility so inspection
        # violations (facility_source_id=well-{permit}) can link to it.
        self._inspection_rows = self._download_csv(_INSPECTION_DATASET, "Oil & Gas Well Inspections")
        well_count = 0
        for row in self._inspection_rows:
            try:
                facility = map_well_facility(row)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    well_count += 1
            except Exception as e:
                logger.warning("Skipping well facility: %s", e)
        logger.info("Unique O&G well facilities: %s", well_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from PA DEP Oil & Gas Well Inspections.

        Only inspections with violation results are yielded.
        PA DEP only covers Pennsylvania. Returns empty for non-PA states.
        """
        if state.upper() != "PA":
            logger.warning("Skipping %s (PA DEP is Pennsylvania-only)", state)
            return

        rows = self._inspection_rows or self._download_csv(_INSPECTION_DATASET, "Oil & Gas Well Inspections")
        violation_count = 0
        for row in rows:
            try:
                if not is_violation_result(row.get("INSPECTION_RESULT_DESC")):
                    continue
                yield map_inspection(row)
                violation_count += 1
            except Exception as e:
                logger.warning("Skipping inspection: %s", e)
        logger.info("Yielded %s inspection violations", violation_count)

    def close(self) -> None:
        self._client.close()
