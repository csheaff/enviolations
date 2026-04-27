from __future__ import annotations

import re
import time
from typing import Iterator
import logging

import httpx

from ..config import EPA_API_KEY, EPA_ECHO_BASE_URL, RATE_LIMIT_DELAY
from ..models import Facility, Violation
from ..normalize.sdwa_mapper import has_violation, map_facility, map_violation
from .base import DataSource

logger = logging.getLogger(__name__)

PAGE_SIZE = 5000

# Regex to strip trailing county-type suffixes that the ECHO p_co filter
# doesn't need (e.g. "Orleans Parish" → "Orleans", "Juneau City and Borough" → "Juneau").
# The ECHO API matches on the base county/borough name without these qualifiers.
_COUNTY_SUFFIX_RE = re.compile(
    r"\s+(?:Parish|County|Borough|Census Area|Municipality|City and Borough)$",
    re.IGNORECASE,
)

def _normalize_county_name(county: str) -> str:
    """Strip state-specific county suffixes for use with the ECHO p_co filter.

    Examples:
      "Orleans Parish" → "Orleans"
      "Juneau City and Borough" → "Juneau"
      "Fairbanks North Star Borough" → "Fairbanks North Star"
      "Autauga" → "Autauga" (no change)
    """
    return _COUNTY_SUFFIX_RE.sub("", county.strip()).strip()

class EPASDWASource(DataSource):
    """Connector for the EPA ECHO Safe Drinking Water Act REST API.

    Two-step flow (note: uses get_systems, not get_facilities):
    1. sdw_rest_services.get_systems → QID + row count
    2. sdw_rest_services.get_qid → paginated water system data

    Key difference: response key is "WaterSystems" not "Facilities".

    Coordinates: SDWA has no lat/lon in its own API. After downloading all
    water systems, this source fetches coordinates from the EPA ECHO GeoJSON
    endpoint (county-by-county) and injects them by RegistryID matching.
    This enables SDWA facilities to appear in geographic radius searches.
    """

    name = "epa_sdwa"

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

    # Sentinel returned by _search when the ECHO API rejects the query due
    # to hitting the per-request queryset limit (~100K facilities).  Callers
    # that want to distinguish "no results" from "too many results" can check
    # for this value and fall back to a more targeted query.
    _QUERYSET_EXCEEDED = "QUERYSET_EXCEEDED"

    def _search(self, endpoint: str, params: dict) -> tuple[str | None, int]:
        """Call a search endpoint. Returns (QID, total_rows).

        Returns (_QUERYSET_EXCEEDED, 0) when the EPA API rejects the request
        with a queryset-limit error so callers can fall back to sub-queries.
        """
        self._rate_limit()
        params["output"] = "JSON"
        resp = self._client.get(endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("Results", {})
        if "Error" in results:
            msg = results['Error'].get('ErrorMessage', str(results['Error']))
            if "Queryset Limit" in msg:
                return self._QUERYSET_EXCEEDED, 0
            logger.error("API error: %s", msg)
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
                    "/echo/sdw_rest_services.get_qid",
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

    def _fetch_geojson_coords(self, qid: str) -> dict[str, tuple[float, float]]:
        """Fetch RegistryID → (lat, lon) from the ECHO GeoJSON endpoint for a QID.

        Returns an empty dict if the request fails or times out.
        GeoJSON coordinates are [lon, lat] per RFC 7946.
        """
        self._rate_limit()
        try:
            resp = self._client.get(
                "/echo/echo_rest_services.get_geojson",
                params={"qid": qid, "output": "GEOJSON"},
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
            coords: dict[str, tuple[float, float]] = {}
            for feature in data.get("features", []):
                geom = feature.get("geometry") or {}
                c = geom.get("coordinates")
                if not c or len(c) < 2:
                    continue
                rid = (feature.get("properties") or {}).get("RegistryID")
                if rid:
                    # GeoJSON: [lon, lat]
                    coords[rid] = (float(c[1]), float(c[0]))
            return coords
        except Exception as e:
            logger.error("GeoJSON coord fetch failed: %s", e)
            return {}

    def _fetch_echo_coords_by_county(
        self, state: str, counties: list[str]
    ) -> dict[str, tuple[float, float]]:
        """Fetch ECHO GeoJSON coordinates for facilities in specific counties.

        SDWA has no lat/lon in its own API. This method queries the EPA ECHO
        echo_rest_services endpoint by county to get actual facility coordinates,
        then returns a {RegistryID: (lat, lon)} dict for coordinate injection.

        Uses county-level queries to avoid GeoJSON timeouts that occur with
        state-level queries for large states (LA, TX, etc.).

        For large counties that exceed the ECHO queryset limit (e.g. Los Angeles),
        falls back to sdw_rest_services.get_systems with a county filter, which
        only returns drinking water systems and stays well under the limit.
        """
        all_coords: dict[str, tuple[float, float]] = {}
        for raw_county in counties:
            county = _normalize_county_name(raw_county)
            if not county:
                continue
            try:
                qid, total = self._search(
                    "/echo/echo_rest_services.get_facilities",
                    {"p_st": state, "p_co": county, "p_act": "Y"},
                )
                if qid == self._QUERYSET_EXCEEDED or not qid or total == 0:
                    if qid == self._QUERYSET_EXCEEDED:
                        logger.info(
                            "%s/%s: ECHO queryset limit hit, falling back to SDW county query",
                            state, county,
                        )
                    # Fallback: query sdw_rest_services directly for this county.
                    # Returns only drinking water systems so result count is small.
                    qid, total = self._search(
                        "/echo/sdw_rest_services.get_systems",
                        {"p_st": state, "p_co": county, "p_act": "Y"},
                    )
                    if not qid or qid == self._QUERYSET_EXCEEDED or total == 0:
                        continue
                coords = self._fetch_geojson_coords(qid)
                all_coords.update(coords)
                logger.info("%s: %s coords from %s facilities", county, len(coords), total)
            except Exception as e:
                logger.error("Failed coord fetch for county %s: %s", county, e)
                continue
        return all_coords

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield SDWA water system facilities with geocoded coordinates.

        Downloads all water systems from sdw_rest_services, then fetches
        coordinates from the EPA ECHO GeoJSON endpoint (county-by-county)
        and injects them by RegistryID before yielding.
        """
        logger.info("Searching SDWA water systems in %s (active)...", state)
        qid, total = self._search(
            "/echo/sdw_rest_services.get_systems",
            {"p_st": state, "p_act": "Y"},
        )
        if not qid:
            logger.info("No QID returned for SDWA search")
            return

        logger.info("Found %s water systems, downloading...", total)
        all_facilities: list[Facility] = []
        for row in self._paginate(qid, total, "WaterSystems"):
            try:
                all_facilities.append(map_facility(row))
            except Exception as e:
                logger.warning("Skipping water system: %s", e)

        # Collect distinct counties for coordinate lookup.
        # CountiesServed can be a comma-separated list; take the first one.
        county_set: set[str] = set()
        for fac in all_facilities:
            if fac.county:
                first_county = fac.county.split(",")[0].strip()
                if first_county:
                    county_set.add(first_county)

        if county_set:
            logger.info("Fetching coordinates for %s counties in %s...", len(county_set), state)
            coord_map = self._fetch_echo_coords_by_county(state, sorted(county_set))
            logger.info("Fetched %s total coordinates for %s", len(coord_map), state)

            # Inject coordinates into facilities by RegistryID (= source_id)
            geocoded = 0
            result: list[Facility] = []
            for fac in all_facilities:
                coords = coord_map.get(fac.source_id)
                if coords and fac.lat is None:
                    lat, lon = coords
                    result.append(fac.model_copy(update={"lat": lat, "lon": lon}))
                    geocoded += 1
                else:
                    result.append(fac)
            logger.info("Geocoded %s/%s SDWA facilities in %s", geocoded, len(all_facilities), state)
            yield from result
        else:
            yield from all_facilities

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Fetch SDWA violations from water system compliance fields."""
        logger.info("Searching SDWA compliance in %s...", state)
        qid, total = self._search(
            "/echo/sdw_rest_services.get_systems",
            {"p_st": state, "p_act": "Y"},
        )
        if not qid:
            logger.info("No QID returned for SDWA compliance search")
            return

        logger.info("Found %s water systems, filtering actual violations...", total)
        kept = 0
        for row in self._paginate(qid, total, "WaterSystems"):
            if not has_violation(row):
                continue
            try:
                yield map_violation(row)
                kept += 1
            except Exception as e:
                logger.warning("Skipping violation: %s", e)
        logger.info("%s: %s actual violations from %s water systems", state, kept, total)

    def close(self) -> None:
        self._client.close()
