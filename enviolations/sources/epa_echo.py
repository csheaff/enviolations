from __future__ import annotations

import time
from typing import Iterator
import logging

import httpx

from ..config import EPA_API_KEY, EPA_ECHO_BASE_URL, RATE_LIMIT_DELAY
from ..models import Facility, Violation
from ..normalize.echo_mapper import has_violation, map_facility, map_violation
from .base import DataSource

logger = logging.getLogger(__name__)

PAGE_SIZE = 5000

class EPAEchoSource(DataSource):
    """Connector for the EPA ECHO REST API.

    Two-step flow:
    1. get_facilities (search) → returns a QID + row count
    2. get_qid (paginate) → returns JSON facility data, PAGE_SIZE per page

    Longitude workaround:
    The JSON response includes FacLat but omits FacLong for ~70% of records
    (an EPA bug). The GeoJSON endpoint for the same QID returns both
    coordinates, so we fetch lon from GeoJSON and inject it into each row
    before the mapper sees it (inject_coords=True).

    For large states (>10K facilities), the state-level GeoJSON request
    times out or returns truncated data. In that case we fall back to
    county-level GeoJSON: discover county names from the first few pages
    of JSON results (_discover_counties), then fetch GeoJSON per county.
    CA and FL have hardcoded county lists for the QUERYSET_EXCEEDED path
    where no QID is available to discover from.
    """

    name = "epa_echo"

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

    # States where facility counts are large enough that a state-level GeoJSON
    # request times out. County-level GeoJSON is used for these states.
    _LARGE_STATE_COUNTIES: dict[str, list[str]] = {
        "CA": [
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
        ],
        "FL": [
            "Alachua", "Baker", "Bay", "Bradford", "Brevard", "Broward",
            "Calhoun", "Charlotte", "Citrus", "Clay", "Collier", "Columbia",
            "DeSoto", "Dixie", "Duval", "Escambia", "Flagler", "Franklin",
            "Gadsden", "Gilchrist", "Glades", "Gulf", "Hamilton", "Hardee",
            "Hendry", "Hernando", "Highlands", "Hillsborough", "Holmes",
            "Indian River", "Jackson", "Jefferson", "Lafayette", "Lake", "Lee",
            "Leon", "Levy", "Liberty", "Madison", "Manatee", "Marion", "Martin",
            "Miami-Dade", "Monroe", "Nassau", "Okaloosa", "Okeechobee", "Orange",
            "Osceola", "Palm Beach", "Pasco", "Pinellas", "Polk", "Putnam",
            "Santa Rosa", "Sarasota", "Seminole", "St. Johns", "St. Lucie",
            "Sumter", "Suwannee", "Taylor", "Union", "Volusia", "Wakulla",
            "Walton", "Washington",
        ],
    }

    def _get_counties(self, state: str) -> list[str]:
        """Return county list for known large states; empty list otherwise."""
        return self._LARGE_STATE_COUNTIES.get(state, [])

    def _discover_counties(self, qid: str, total: int) -> list[str]:
        """Discover counties by reading facility pages from an existing QID.

        For states without a hardcoded county list, reads the first few pages
        of JSON results to extract distinct FacCounty values. Reuses the
        already-allocated QID (pages can be re-read by _paginate later).
        """
        counties: set[str] = set()
        pages_to_scan = min(3, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        for p in range(1, pages_to_scan + 1):
            rows = self._get_page(qid, p, "Facilities")
            for r in rows:
                county = (r.get("FacCounty") or "").strip()
                if county:
                    counties.add(county)
        logger.info("Discovered %d counties from %d pages", len(counties), pages_to_scan)
        return sorted(counties)

    def _fetch_coords_by_county(
        self, state: str, counties: list[str]
    ) -> dict[str, float]:
        """Fetch longitude coordinates county-by-county via GeoJSON.

        Used for states where the state-level GeoJSON endpoint times out due
        to large facility counts (e.g. FL with 53K facilities). Queries
        echo_rest_services.get_facilities per county and fetches GeoJSON for
        each county QID, then merges into a single {RegistryID: lon} dict.
        """
        all_coords: dict[str, float] = {}
        for county in counties:
            try:
                cqid, ctotal = self._search(
                    "/echo/echo_rest_services.get_facilities",
                    {"p_st": state, "p_co": county, "p_act": "Y"},
                )
                if not cqid or ctotal == 0:
                    continue
                coords = self._fetch_coords(cqid)
                all_coords.update(coords)
                logger.info("%s: %s coords from %s facilities", county, len(coords), ctotal)
            except Exception as e:
                logger.error("Failed coord fetch for county %s: %s", county, e)
                continue
        return all_coords

    def _get_page(self, qid: str, pageno: int, key: str) -> list[dict]:
        """Fetch one page of results using get_qid. Retries on transient errors."""
        for attempt in range(3):
            self._rate_limit()
            try:
                resp = self._client.get(
                    "/echo/echo_rest_services.get_qid",
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

    def _fetch_coords(self, qid: str) -> dict[str, float]:
        """Fetch longitude from GeoJSON endpoint for a QID.

        The ECHO get_facilities JSON response does not include FacLong.
        The GeoJSON endpoint returns coordinates for the same QID.
        Returns {RegistryID: longitude}.
        """
        self._rate_limit()
        try:
            resp = self._client.get(
                "/echo/echo_rest_services.get_geojson",
                params={"qid": qid, "output": "GEOJSON"},
                timeout=300.0,
            )
            resp.raise_for_status()
            data = resp.json()
            coords = {}
            for feature in data.get("features", []):
                rid = feature.get("properties", {}).get("RegistryID")
                geom = feature.get("geometry") or {}
                c = geom.get("coordinates")
                if rid and c and len(c) >= 2:
                    coords[rid] = c[0]  # GeoJSON: [lon, lat]
            return coords
        except Exception as e:
            logger.error("GeoJSON coord fetch failed: %s", e)
            return {}

    def _fetch_with_county_fallback(self, state: str, endpoint: str, params: dict, key: str, mapper, label: str, inject_coords: bool = False) -> Iterator:
        """Search with automatic county-based fallback for large states."""
        qid, total = self._search(endpoint, {**params, "p_st": state})
        if qid == "QUERYSET_EXCEEDED":
            counties = self._get_counties(state)
            if not counties:
                logger.info("%s: queryset exceeded for %s and no county list available", label, state)
                return
            logger.info("%s: queryset exceeded for %s, splitting into %s county queries...", label, state, len(counties))
            for county in counties:
                cqid, ctotal = self._search(endpoint, {**params, "p_st": state, "p_co": county})
                if not cqid or cqid == "QUERYSET_EXCEEDED":
                    logger.warning("Skipping county %s (no results or too large)", county)
                    continue
                logger.info("%s: %s records...", county, ctotal)
                coord_map = self._fetch_coords(cqid) if inject_coords else {}
                if inject_coords:
                    logger.info("Fetched %s coordinates for %s", len(coord_map), county)
                for row in self._paginate(cqid, ctotal, key):
                    if inject_coords and coord_map:
                        rid = row.get("RegistryID", "")
                        if rid in coord_map:
                            row["FacLong"] = str(coord_map[rid])
                    try:
                        yield mapper(row)
                    except Exception as e:
                        logger.warning("Skipping %s: %s", label, e)
            return
        if not qid:
            logger.info("No QID returned for %s search", label)
            return
        logger.info("Found %s %s, downloading...", total, label)
        coord_map = self._fetch_coords(qid) if inject_coords else {}
        if inject_coords:
            logger.info("Fetched %s coordinates via GeoJSON", len(coord_map))
            # For large states the state-level GeoJSON request may time out or
            # return truncated results. Fall back to county-level GeoJSON
            # injection when coordinate coverage is below 5% of total facilities.
            if total > 0 and len(coord_map) < total * 0.05:
                counties = self._get_counties(state)
                if not counties:
                    counties = self._discover_counties(qid, total)
                if counties:
                    logger.info("Low coord coverage (%d/%d), fetching county-level GeoJSON for %s (%d counties)...", len(coord_map), total, state, len(counties))
                    coord_map = self._fetch_coords_by_county(state, counties)
                    logger.info("County-level GeoJSON: %s coordinates", len(coord_map))
        for row in self._paginate(qid, total, key):
            if inject_coords and coord_map:
                rid = row.get("RegistryID", "")
                if rid in coord_map:
                    row["FacLong"] = str(coord_map[rid])
            try:
                yield mapper(row)
            except Exception as e:
                logger.warning("Skipping %s: %s", label, e)

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        logger.info("Searching facilities in %s (active permits)...", state)
        yield from self._fetch_with_county_fallback(
            state, "/echo/echo_rest_services.get_facilities",
            {"p_act": "Y"}, "Facilities", map_facility, "facility",
            inject_coords=True,
        )

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Fetch CWA violations, filtering out ~90% of records that are
        clean compliance checks (SNC status "No", no violation status).
        """
        logger.info("Searching CWA compliance in %s...", state)
        total = 0
        kept = 0
        for row in self._fetch_with_county_fallback(
            state, "/echo/cwa_rest_services.get_facilities",
            {"p_act": "Y"}, "Facilities", lambda r: r, "CWA row",
        ):
            total += 1
            if has_violation(row):
                try:
                    yield map_violation(row)
                    kept += 1
                except Exception as e:
                    logger.warning("Skipping CWA violation: %s", e)
        logger.info("%s: %s actual violations from %s CWA records", state, kept, total)

    def close(self) -> None:
        self._client.close()
