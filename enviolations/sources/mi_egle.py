"""Connector for MI EGLE (Michigan Dept. of Environment, Great Lakes, and Energy).

Downloads JSON data from MI EGLE's ArcGIS REST services at
gisagoegle.state.mi.us.
Facility datasets:
  - MmdOpenData/MapServer/0: Materials Management Facilities → facilities
  - RRDOpenData/MapServer/0: Part 201 Contamination Sites → facilities
Violation datasets:
  - Planet Detroit Air Quality Violation Notices CSV (2018-present) → facilities + violations (~1.8K)

This source only covers Michigan (state="MI").
"""

from __future__ import annotations

import csv
import io
import time
from typing import Iterator
import logging

import httpx

from ..models import Facility, Violation
from ..normalize.mi_egle_mapper import (
    map_air_violation,
    map_air_violation_facility,
    map_mmd_facility,
    map_rrd_site,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints
_MMD_URL = (
    "https://gisagoegle.state.mi.us/arcgis/rest/services"
    "/EGLE/MmdOpenData/MapServer/0/query"
)
_RRD_URL = (
    "https://gisagoegle.state.mi.us/arcgis/rest/services"
    "/EGLE/RRDOpenData/MapServer/0/query"
)

_AIR_VN_CSV_URL = (
    "https://raw.githubusercontent.com/Planet-Detroit"
    "/air-permit-violation-dashboard/main/output"
    "/EGLE-AQD-Violation-Notices-2018-Present.csv"
)

_MMD_PAGE_SIZE = 1000
_RRD_PAGE_SIZE = 5000

class MIEGLESource(ArcGISSource):
    """Connector for MI EGLE ArcGIS REST services."""

    name = "mi_egle"
    page_size = 1000

    def __init__(self) -> None:
        super().__init__()
        self._air_vn_records: list[dict] | None = None

    def _fetch_air_vn_csv(self) -> list[dict]:
        """Fetch the Planet Detroit Air Violation Notices CSV."""
        if self._air_vn_records is not None:
            return self._air_vn_records

        logger.info("Fetching Air Quality Violation Notices CSV...")
        for attempt in range(3):
            try:
                resp = self._client.get(_AIR_VN_CSV_URL, follow_redirects=True)
                resp.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    logger.error("Error fetching CSV: %s, retrying in %ss...", e, wait)
                    time.sleep(wait)
                else:
                    logger.error("Failed to fetch CSV after 3 attempts: %s", e)
                    self._air_vn_records = []
                    return []

        reader = csv.DictReader(io.StringIO(resp.text))
        records = list(reader)
        self._air_vn_records = records
        logger.info("Got %s air violation records", len(records))
        return records

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from MI EGLE MMD, RRD, and Air VN datasets.

        MI EGLE only covers Michigan. Returns empty for non-MI states.
        """
        if state.upper() != "MI":
            logger.warning("Skipping %s (MI EGLE is Michigan-only)", state)
            return

        seen_ids: set[str] = set()

        # Materials Management Division facilities
        mmd_features = self._query_features(_MMD_URL, "MMD Facilities", page_size=_MMD_PAGE_SIZE)
        for feat in mmd_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_mmd_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping MMD facility: %s", e)

        mmd_count = len(seen_ids)
        logger.info("Unique MMD facilities: %s", mmd_count)

        # RRD Part 201 Contamination Sites
        rrd_features = self._query_features(_RRD_URL, "RRD Part 201 Sites", page_size=_RRD_PAGE_SIZE)
        for feat in rrd_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_rrd_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping RRD site: %s", e)

        rrd_count = len(seen_ids) - mmd_count
        logger.info("Unique RRD Part 201 sites: %s", rrd_count)

        # Air Quality Violation Notice facilities (Planet Detroit CSV)
        prev_count = len(seen_ids)
        air_records = self._fetch_air_vn_csv()
        for rec in air_records:
            try:
                facility = map_air_violation_facility(rec)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping air VN facility: %s", e)

        air_count = len(seen_ids) - prev_count
        logger.info("Unique air VN facilities: %s", air_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield air quality violations from Planet Detroit CSV."""
        if state.upper() != "MI":
            logger.warning("Skipping %s (MI EGLE is Michigan-only)", state)
            return

        air_records = self._fetch_air_vn_csv()
        vio_count = 0
        for rec in air_records:
            try:
                violation = map_air_violation(rec)
                if violation.source_id:
                    yield violation
                    vio_count += 1
            except Exception as e:
                logger.warning("Skipping air violation: %s", e)

        logger.info("Air quality violations: %s", vio_count)

