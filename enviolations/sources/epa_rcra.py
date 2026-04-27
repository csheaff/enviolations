from __future__ import annotations

import time
from typing import Iterator
import logging

import httpx

from ..config import EPA_API_KEY, EPA_ECHO_BASE_URL, RATE_LIMIT_DELAY
from ..models import Facility, Violation
from ..normalize.rcra_mapper import has_violation, map_facility, map_violation
from .base import DataSource

logger = logging.getLogger(__name__)

PAGE_SIZE = 5000

class EPARCRASource(DataSource):
    """Connector for the EPA ECHO RCRA REST API (hazardous waste).

    Uses the same two-step QID flow as the main ECHO API but with
    RCRA-specific endpoints and field names:
    1. rcra_rest_services.get_facilities → QID + row count
    2. rcra_rest_services.get_qid → paginated facility data
    """

    name = "epa_rcra"

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
            msg = results["Error"].get("ErrorMessage", str(results["Error"]))
            if "Queryset Limit" in msg:
                return "QUERYSET_EXCEEDED", 0
            logger.error("API error: %s", msg)
            return None, 0
        qid = results.get("QueryID")
        total = int(results.get("QueryRows", 0))
        return qid, total

    def _get_counties(self, state: str) -> list[str]:
        """Get list of counties for known large states."""
        if state == "CA":
            return [
                "Alameda", "Alpine", "Amador", "Butte", "Calaveras", "Colusa",
                "Contra Costa", "Del Norte", "El Dorado", "Fresno", "Glenn",
                "Humboldt", "Imperial", "Inyo", "Kern", "Kings", "Lake", "Lassen",
                "Los Angeles", "Madera", "Marin", "Mariposa", "Mendocino", "Merced",
                "Modoc", "Mono", "Monterey", "Napa", "Nevada", "Orange", "Placer",
                "Plumas", "Riverside", "Sacramento", "San Benito", "San Bernardino",
                "San Diego", "San Francisco", "San Joaquin", "San Luis Obispo",
                "San Mateo", "Santa Barbara", "Santa Clara", "Santa Cruz", "Shasta",
                "Sierra", "Siskiyou", "Solano", "Sonoma", "Stanislaus", "Sutter",
                "Tehama", "Trinity", "Tulare", "Tuolumne", "Ventura", "Yolo", "Yuba",
            ]
        return []

    def _get_page(self, qid: str, pageno: int, key: str) -> list[dict]:
        """Fetch one page of results using get_qid. Retries on transient errors."""
        for attempt in range(3):
            self._rate_limit()
            try:
                resp = self._client.get(
                    "/echo/rcra_rest_services.get_qid",
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

    def _fetch_with_county_fallback(self, state: str, endpoint: str, params: dict, key: str, mapper, label: str) -> Iterator:
        """Search with automatic county-based fallback for large states.

        For states known to cause full-state query timeouts (e.g. CA), skips
        the full-state query entirely and goes straight to county-level queries.
        For other states, falls back to county queries only if the API returns
        QUERYSET_EXCEEDED.
        """
        counties = self._get_counties(state)
        if counties:
            logger.info("%s: large state %s — using county-level queries directly (%s counties)...", label, state, len(counties))
            for county in counties:
                cqid, ctotal = self._search(endpoint, {**params, "p_st": state, "p_co": county})
                if not cqid or cqid == "QUERYSET_EXCEEDED":
                    logger.warning("Skipping county %s (no results or too large)", county)
                    continue
                logger.info("%s %s: %s records...", county, label, ctotal)
                for row in self._paginate(cqid, ctotal, key):
                    try:
                        yield mapper(row)
                    except Exception as e:
                        logger.warning("Skipping %s: %s", label, e)
            return
        qid, total = self._search(endpoint, {**params, "p_st": state})
        if qid == "QUERYSET_EXCEEDED":
            logger.info("%s: queryset exceeded for %s and no county list available", label, state)
            return
        if not qid:
            logger.info("No QID returned for %s search", label)
            return
        logger.info("Found %s %s, downloading...", total, label)
        for row in self._paginate(qid, total, key):
            try:
                yield mapper(row)
            except Exception as e:
                logger.warning("Skipping %s: %s", label, e)

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        logger.info("Searching RCRA facilities in %s (active)...", state)
        yield from self._fetch_with_county_fallback(
            state, "/echo/rcra_rest_services.get_facilities",
            {"p_act": "Y"}, "Facilities", map_facility, "RCRA facility",
        )

    def _iter_raw_rows(self, state: str) -> Iterator[dict]:
        """Yield raw facility JSON rows (for violation filtering)."""
        yield from self._fetch_with_county_fallback(
            state, "/echo/rcra_rest_services.get_facilities",
            {"p_act": "Y"}, "Facilities", lambda row: row, "RCRA row",
        )

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Fetch RCRA violations by re-using facility data with compliance fields.

        Filters out facilities with no actual violations (~99.5% of records
        have status "No Violation Identified") before mapping to Violation models.
        """
        logger.info("Searching RCRA compliance in %s...", state)
        total = 0
        kept = 0
        for row in self._iter_raw_rows(state):
            total += 1
            if has_violation(row):
                try:
                    yield map_violation(row)
                    kept += 1
                except Exception as e:
                    logger.warning("Skipping RCRA violation: %s", e)
        logger.info("%s: %s actual violations from %s facilities", state, kept, total)

    def close(self) -> None:
        self._client.close()
