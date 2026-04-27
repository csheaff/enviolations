from __future__ import annotations

import time
from typing import Iterator
import logging

import httpx

from ..config import EPA_API_KEY, EPA_ECHO_BASE_URL, RATE_LIMIT_DELAY
from ..models import Facility, Violation
from ..normalize.caa_mapper import has_violation, map_facility, map_violation
from .base import DataSource

logger = logging.getLogger(__name__)

PAGE_SIZE = 5000

class EPACAASource(DataSource):
    """Connector for the EPA ECHO Clean Air Act REST API.

    Two-step flow:
    1. air_rest_services.get_facilities → QID + row count
    2. air_rest_services.get_qid → paginated facility data
    """

    name = "epa_caa"

    def __init__(self) -> None:
        auth = (EPA_API_KEY, "") if EPA_API_KEY else None
        self._client = httpx.Client(
            base_url=EPA_ECHO_BASE_URL,
            auth=auth,
            timeout=120.0,
        )
        self._last_request = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request = time.monotonic()

    def _search(self, endpoint: str, params: dict) -> tuple[str | None, int]:
        """Call a search endpoint. Returns (QID, total_rows)."""
        self._rate_limit()
        params["output"] = "JSON"
        resp = self._client.get(endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("Results", {})
        if "Error" in results:
            logger.error("API error: %s", results['Error'].get('ErrorMessage', results['Error']))
            return None, 0
        qid = results.get("QueryID")
        total = int(results.get("QueryRows", 0))
        return qid, total

    def _get_page(self, qid: str, pageno: int, key: str) -> list[dict]:
        """Fetch one page of results using get_qid. Retries on transient errors."""
        for attempt in range(3):
            self._rate_limit()
            try:
                resp = self._client.get(
                    "/echo/air_rest_services.get_qid",
                    params={"qid": qid, "output": "JSON", "pageno": pageno, "pagesize": PAGE_SIZE},
                    timeout=300.0,
                )
                if resp.status_code >= 500 and attempt < 2:
                    wait = 15 * (attempt + 1)
                    logger.error("Server error %s on page %s, retrying in %ss...", resp.status_code, pageno, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data.get("Results", {}).get(key, [])
            except httpx.TimeoutException:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    logger.info("Timeout on page %s, retrying in %ss...", pageno, wait)
                    time.sleep(wait)
                else:
                    raise
        return []

    def _paginate(self, qid: str, total: int, key: str) -> Iterator[dict]:
        """Yield all rows across pages."""
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        for pageno in range(1, pages + 1):
            logger.info("Page %s/%s...", pageno, pages)
            rows = self._get_page(qid, pageno, key)
            if not rows:
                break
            yield from rows

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        logger.info("Searching CAA facilities in %s (active)...", state)
        qid, total = self._search(
            "/echo/air_rest_services.get_facilities",
            {"p_st": state, "p_act": "Y"},
        )
        if not qid:
            logger.info("No QID returned for CAA facility search")
            return

        logger.info("Found %s CAA facilities, downloading...", total)
        for row in self._paginate(qid, total, "Facilities"):
            try:
                yield map_facility(row)
            except Exception as e:
                logger.warning("Skipping facility: %s", e)

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Fetch CAA violations by re-using facility data with compliance fields.

        The CAA facility endpoint includes compliance/violation fields
        (AIRComplStatus, AIRHpvStatus, AIRQtrsWithViol, etc.) so we search
        the same endpoint but yield Violation records from the compliance columns.
        """
        logger.info("Searching CAA compliance in %s...", state)
        qid, total = self._search(
            "/echo/air_rest_services.get_facilities",
            {"p_st": state, "p_act": "Y"},
        )
        if not qid:
            logger.info("No QID returned for CAA compliance search")
            return

        logger.info("Found %s CAA records, filtering actual violations...", total)
        kept = 0
        for row in self._paginate(qid, total, "Facilities"):
            if not has_violation(row):
                continue
            try:
                yield map_violation(row)
                kept += 1
            except Exception as e:
                logger.warning("Skipping violation: %s", e)
        logger.info("%s: %s actual violations from %s facility records", state, kept, total)

    def close(self) -> None:
        self._client.close()
