"""Connector for NE DEE (Nebraska Dept of Environment and Energy) data.

Downloads facility data from Nebraska DEQ's ArcGIS MapServer at
deqmaps.nebraska.gov.

One facility dataset:
  - DEQ Coordinates (DEQ/MapServer/0): ~70K regulated facility points with
    program codes and coordinates

The server is ArcGIS 10.41 and does not support standard pagination. We
query using FID ranges instead.

NE DEE does not publish facility names/addresses via this service;
only coordinates, program codes, and facility IDs are available.

One violation dataset:
  - LUST/Spill database CSV (~21K records) from deq-iis.ne.gov with
    petroleum release incidents, discovery dates, material types, and
    investigation status.

This source only covers Nebraska (state="NE").
"""

from __future__ import annotations

import csv
import io
import time
from typing import Iterator
import logging

import httpx

from ..models import Facility, Violation
from ..normalize.ne_dee_mapper import map_facility, map_lust_facility, map_lust_spill
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://deqmaps.nebraska.gov/arcgis/rest/services/DEQ/MapServer"
_QUERY_URL = f"{_BASE}/0/query"

_LUST_CSV_URL = (
    "https://deq-iis.ne.gov/zs/spillfac/download_known_spillfac.php"
    "?county=&city=&NDEQ=&address=&name=&searchtype=known&status=&type="
)

_BATCH_SIZE = 5000

class NEDEESource(ArcGISSource):
    """Connector for Nebraska DEE ArcGIS MapServer."""

    name = "ne_dee"

    def __init__(self) -> None:
        super().__init__()
        self._lust_rows: list[dict] | None = None

    def _get_max_fid(self) -> int:
        """Get the max FID to know our query range."""
        self._rate_limit()
        params = {
            "where": "1=1",
            "returnCountOnly": "true",
            "f": "json",
        }
        try:
            resp = self._client.get(_QUERY_URL, params=params, follow_redirects=True)
            resp.raise_for_status()
            data = resp.json()
            return data.get("count", 0)
        except Exception:
            return 100000  # fallback estimate

    def _query_fid_range(self, fid_start: int, fid_end: int, label: str) -> list[dict]:
        """Query features by FID range (pagination workaround for old ArcGIS)."""
        self._rate_limit()
        params = {
            "where": f"FID>={fid_start} AND FID<{fid_end}",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
        }

        for attempt in range(3):
            try:
                logger.info("Querying %s (FID %s-%s)...", label, fid_start, fid_end)
                resp = self._client.get(_QUERY_URL, params=params, follow_redirects=True)

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
                    return []

        data = resp.json()

        if "error" in data:
            err = data["error"]
            logger.error("ArcGIS error: %s", err.get('message', err))
            return []

        features = data.get("features", [])
        return features

    def _fetch_lust_csv(self) -> list[dict]:
        """Download and parse the LUST/spill CSV. Cached for reuse."""
        if self._lust_rows is not None:
            return self._lust_rows

        logger.info("Downloading LUST/spill database CSV...")
        self._rate_limit()

        for attempt in range(3):
            try:
                resp = self._client.get(_LUST_CSV_URL, follow_redirects=True)
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
                    logger.error("Failed to download LUST CSV after 3 attempts (timeout)")
                    self._lust_rows = []
                    return self._lust_rows
            except Exception as e:
                logger.error("LUST CSV download error: %s", e)
                self._lust_rows = []
                return self._lust_rows

        try:
            text = resp.content.decode("utf-8")
        except UnicodeDecodeError:
            text = resp.content.decode("latin-1")

        # The CSV has leading spaces in all column headers after the first
        # (e.g. " Facility Name", " Facility City"). Strip them so mapper
        # lookups using clean header names work correctly.
        reader = csv.reader(io.StringIO(text))
        headers = [h.strip() for h in next(reader, [])]
        self._lust_rows = [dict(zip(headers, row)) for row in reader]
        logger.info("Parsed %s LUST CSV rows", len(self._lust_rows))
        return self._lust_rows

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "NE":
            logger.warning("Skipping %s (NE DEE is Nebraska-only)", state)
            return

        total_count = self._get_max_fid()
        logger.info("Total facility count: %s", total_count)

        seen_ids: set[str] = set()
        fid_start = 0

        while fid_start < total_count + _BATCH_SIZE:
            fid_end = fid_start + _BATCH_SIZE
            features = self._query_fid_range(fid_start, fid_end, "Facilities")

            if not features:
                fid_start = fid_end
                continue

            for feat in features:
                try:
                    facility = map_facility(feat)
                    if facility.source_id and facility.source_id not in seen_ids:
                        seen_ids.add(facility.source_id)
                        yield facility
                except Exception as e:
                    logger.warning("Skipping facility: %s", e)

            logger.info("Got %s features (total unique: %s)", len(features), len(seen_ids))
            fid_start = fid_end

        arcgis_count = len(seen_ids)
        logger.info("ArcGIS facilities: %s", arcgis_count)

        # Also create facilities from LUST CSV so violations can link
        lust_rows = self._fetch_lust_csv()
        lust_count = 0
        for row in lust_rows:
            try:
                facility = map_lust_facility(row)
                if facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    lust_count += 1
            except Exception:
                pass

        logger.info("LUST site facilities: %s", lust_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "NE":
            logger.warning("Skipping %s (NE DEE is Nebraska-only)", state)
            return

        lust_rows = self._fetch_lust_csv()
        violation_count = 0
        skip_count = 0

        for row in lust_rows:
            try:
                ndeq_file = (row.get("NDEQ File #") or "").strip()
                if not ndeq_file:
                    skip_count += 1
                    continue
                yield map_lust_spill(row)
                violation_count += 1
            except Exception as e:
                skip_count += 1
                logger.warning("Skipping LUST row: %s", e)

        logger.info("Yielded %s LUST/spill violations" f" (skipped %s)", violation_count, skip_count)

