"""Connector for TCEQ (Texas Commission on Environmental Quality) bulk data.

Downloads CSV data from the Texas Open Data Portal (data.texas.gov) via Socrata.
Three datasets:
  - Central Registry (5 regional datasets) → facilities
  - Notices of Violation (NOV) → violations
  - Notices of Enforcement (NOE) → violations

This source only covers Texas (state="TX").
"""

from __future__ import annotations

import csv
import io
import time
from typing import Iterator
import logging

import httpx

from ..geo import batch_geocode_addresses, normalize_tx_highway_street, _CENSUS_BATCH_SIZE
from ..models import Facility, Violation
from ..normalize.tceq_mapper import (
    build_nov_status_lookup,
    has_residential_indicator,
    is_personal_name,
    map_facility,
    map_noe,
    map_nov,
)
from .base import DataSource

logger = logging.getLogger(__name__)

# Socrata bulk CSV download pattern
_SOCRATA_BASE = "https://data.texas.gov/api/views/{dataset_id}/rows.csv"

# Central Registry datasets are split by TCEQ region
_REGISTRY_DATASETS = [
    ("9iad-hrn8", "Border & Permian Basin"),
    ("msah-s2rv", "Central Texas"),
    ("tzyg-j7q4", "Dallas/Fort Worth"),
    ("5eqq-7nad", "Houston/Beaumont"),
    ("t34q-qzi3", "San Antonio/Austin/Lubbock"),
]

# Violation datasets
_NOV_DATASET = "mwzi-gyw7"
_NOE_DATASET = "rua3-iswk"
# Violation Citations dataset — provides per-violation resolution status for NOVs
_VIOLATION_CITATIONS_DATASET = "gyd4-wuys"

_RATE_LIMIT_DELAY = 2.0

class TCEQSource(DataSource):
    """Connector for TCEQ bulk CSV data from the Texas Open Data Portal."""

    name = "tceq"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=300.0)
        self._last_request = 0.0

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
                reader = csv.DictReader(io.StringIO(resp.text))
                rows = list(reader)
                del resp  # free httpx response buffer
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
        """Yield facilities from TCEQ Central Registry with geocoded coordinates.

        TCEQ only covers Texas. Returns empty for non-TX states.
        Processes one region at a time to limit peak memory (~2 GB vs ~7 GB).
        Geocodes per-region using Census Bureau batch geocoder (up to 9,999/req).
        """
        if state.upper() != "TX":
            logger.warning("Skipping %s (TCEQ is Texas-only)", state)
            return

        seen_rns: set[str] = set()
        personal_registrant_count = 0

        for dataset_id, label in _REGISTRY_DATASETS:
            rows = self._download_csv(dataset_id, f"Central Registry - {label}")

            # Parse and dedup this region's facilities
            region_facilities: list[Facility] = []
            for row in rows:
                try:
                    facility = map_facility(row)
                    if is_personal_name(facility.name):
                        personal_registrant_count += 1
                        continue
                    if facility.source_id and facility.source_id not in seen_rns:
                        seen_rns.add(facility.source_id)
                        region_facilities.append(facility)
                except Exception as e:
                    logger.warning("Skipping facility: %s", e)

            del rows  # free CSV dicts before geocoding

            # Geocode this region's facilities that have address+city but no coords.
            # TX highway addresses normalized before sending (e.g. "FM 1960" →
            # "Farm to Market Road 1960").
            needs_geocode = [
                f for f in region_facilities
                if f.lat is None and f.address and f.city
            ]
            if needs_geocode:
                logger.info(
                    "Geocoding %s/%s facilities in %s...",
                    len(needs_geocode), len(region_facilities), label,
                )
                geocode_index = {f.source_id: f for f in needs_geocode}
                geocoded_total = 0
                for batch_start in range(0, len(needs_geocode), _CENSUS_BATCH_SIZE):
                    batch = needs_geocode[batch_start:batch_start + _CENSUS_BATCH_SIZE]
                    records = [
                        (
                            f.source_id,
                            normalize_tx_highway_street(f.address or ""),
                            f.city or "",
                            f.state or "TX",
                            f.zip_code or "",
                        )
                        for f in batch
                    ]
                    coords = batch_geocode_addresses(records)
                    for rn, (lat, lon) in coords.items():
                        if rn in geocode_index:
                            geocode_index[rn].lat = lat
                            geocode_index[rn].lon = lon
                    geocoded_total += len(coords)
                logger.info("Geocoded %s facilities in %s", geocoded_total, label)
                del needs_geocode, geocode_index

            # Yield this region and free memory before next download
            logger.info("Region %s: %s facilities", label, len(region_facilities))
            yield from region_facilities
            del region_facilities

        if personal_registrant_count:
            logger.info(
                "Skipped %s personal registrants (individual permit holders)",
                personal_registrant_count,
            )
        logger.info("Total unique facilities: %s", len(seen_rns))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from TCEQ NOV and NOE datasets.

        TCEQ only covers Texas. Returns empty for non-TX states.

        Downloads the Violation Citations dataset (gyd4-wuys) to obtain real
        resolution status for NOVs.  NOEs fall back to the date-based heuristic
        because the NOE dataset does not link to violation citation records.
        """
        if state.upper() != "TX":
            logger.warning("Skipping %s (TCEQ is Texas-only)", state)
            return

        # Download Violation Citations to build a per-NOV status lookup.
        # This dataset provides the actual resolution status (Active, Resolved,
        # In Review, etc.) for each violation tracking number, keyed by
        # Notice of Violation ID.  ~105K rows / ~43 MB — manageable in memory.
        citation_rows = self._download_csv(
            _VIOLATION_CITATIONS_DATASET, "Violation Citations"
        )
        nov_status_lookup = build_nov_status_lookup(citation_rows)
        logger.info(
            "Built NOV status lookup: %s entries from %s citation rows",
            len(nov_status_lookup),
            len(citation_rows),
        )
        del citation_rows  # free ~210 MB

        # Notices of Violation
        nov_rows = self._download_csv(_NOV_DATASET, "Notices of Violation")
        nov_count = 0
        for row in nov_rows:
            try:
                yield map_nov(row, nov_status_lookup=nov_status_lookup)
                nov_count += 1
            except Exception as e:
                logger.warning("Skipping NOV: %s", e)
        logger.info("Yielded %s NOV violations", nov_count)
        del nov_rows  # free before next download

        # Notices of Enforcement
        noe_rows = self._download_csv(_NOE_DATASET, "Notices of Enforcement")
        noe_count = 0
        for row in noe_rows:
            try:
                yield map_noe(row)
                noe_count += 1
            except Exception as e:
                logger.warning("Skipping NOE: %s", e)
        logger.info("Yielded %s NOE violations", noe_count)
        del noe_rows

    def close(self) -> None:
        self._client.close()
