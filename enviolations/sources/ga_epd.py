"""Connector for GA EPD (Georgia Environmental Protection Division).

Downloads data from two GA EPD services:
Facility datasets:
  - Hazardous Site Inventory (ArcGIS Online FeatureServer): ~509 sites
  - ENFO Enforcement Orders (enfo.gaepd.org/api): ~22K+ orders → unique facilities
Violation datasets:
  - ENFO Enforcement Orders → violations (22K+ enforcement orders since 1998)

This source only covers Georgia (state="GA").

ENFO Geocoding
--------------
The ENFO API provides only facilityName + county — no address, city, or
coordinates.  During ingest we geocode each unique GA county once via
geocode_address("{county} County, Georgia") using Nominatim and assign the
resulting county centroid to every ENFO facility in that county.  County
centroids are approximate (~10–30 km radius) but give every ENFO facility a
geographic anchor, improving map display and enabling eventual entity
resolution via the geo tier once the threshold is adjusted for county-level
precision.  We also set city = county name (title-cased) so searches can at
least filter by rough region.
"""

from __future__ import annotations

import time
from typing import Iterator
import logging

import httpx

from ..geo import geocode_address
from ..models import Facility, Violation
from ..normalize.ga_epd_mapper import (
    map_enforcement_facility,
    map_enforcement_violation,
    map_hsi_site,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS Online FeatureServer endpoint
_HSI_URL = (
    "https://services1.arcgis.com/p0dLjwtOaJHU8zq2/arcgis/rest/services"
    "/Georgia_EPD_Hazardous_Site_Inventory_2019_07_WFL1/FeatureServer/0/query"
)

_ENFO_URL = "https://enfo.gaepd.org/api/EnforcementOrder"
_ENFO_PAGE_SIZE = 500

# Nominatim rate limit: 1 request per second (usage policy).
_NOMINATIM_DELAY = 1.0


def _geocode_counties(counties: list[str]) -> dict[str, tuple[float, float]]:
    """Geocode each unique GA county once and return a county → (lat, lon) map.

    Uses geocode_address("{county} County, Georgia") via Nominatim.
    Sleeps _NOMINATIM_DELAY seconds between requests to honour rate limits.
    Returns a dict of upper-cased county name → (lat, lon).  Counties that
    fail to geocode are omitted (the facility will have no coordinates).
    """
    county_coords: dict[str, tuple[float, float]] = {}
    for county in sorted(set(counties)):
        if not county:
            continue
        query = f"{county.title()} County, Georgia"
        try:
            result = geocode_address(query)
            if result is not None:
                lat, lon, _ = result
                county_coords[county.upper()] = (lat, lon)
                logger.debug("Geocoded GA county %r -> (%.4f, %.4f)", county, lat, lon)
            else:
                logger.debug("Could not geocode GA county %r", county)
        except Exception as e:
            logger.warning("Error geocoding GA county %r: %s", county, e)
        time.sleep(_NOMINATIM_DELAY)

    logger.info(
        "GA county geocoding: %d/%d counties resolved", len(county_coords), len(set(c for c in counties if c))
    )
    return county_coords


class GAEPDSource(ArcGISSource):
    """Connector for GA EPD ArcGIS Online FeatureServer + ENFO API."""

    name = "ga_epd"

    def __init__(self) -> None:
        super().__init__()
        self._enfo_items: list[dict] | None = None

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from GA EPD Hazardous Site Inventory and ENFO.

        GA EPD only covers Georgia. Returns empty for non-GA states.

        ENFO facilities lack address and coordinate data.  After building the
        unique-facility list, we geocode each distinct county once via
        Nominatim and assign the county centroid to every ENFO facility in
        that county so they appear on the map and can participate in
        geo-proximity entity resolution.
        """
        if state.upper() != "GA":
            logger.warning("Skipping %s (GA EPD is Georgia-only)", state)
            return

        seen_ids: set[str] = set()

        hsi_features = self._query_features(_HSI_URL, "Hazardous Site Inventory")
        for feat in hsi_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_hsi_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping HSI site: %s", e)

        logger.info("Unique HSI facilities: %s", len(seen_ids))

        # ENFO Enforcement Orders → unique facilities
        prev_count = len(seen_ids)
        enfo_items = self._fetch_enfo_orders()
        self._enfo_items = enfo_items

        # Build unique-facility list first so we can geocode counties in bulk
        enfo_facilities: list[Facility] = []
        for item in enfo_items:
            try:
                facility = map_enforcement_facility(item)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    enfo_facilities.append(facility)
            except Exception as e:
                logger.warning("Skipping ENFO facility: %s", e)

        # Geocode each unique county once and inject county-centroid coordinates
        # into facilities that have no lat/lon from the source.
        counties = [f.county for f in enfo_facilities if f.county and f.lat is None]
        if counties:
            county_coords = _geocode_counties(counties)
            geocoded_count = 0
            for fac in enfo_facilities:
                if fac.lat is None and fac.county:
                    coords = county_coords.get(fac.county.upper())
                    if coords:
                        fac.lat, fac.lon = coords
                        # Set city to county name so map display shows a region
                        if not fac.city:
                            fac.city = fac.county.title()
                        geocoded_count += 1
            logger.info(
                "ENFO county geocoding: %d/%d facilities received coordinates",
                geocoded_count, len(enfo_facilities),
            )

        for facility in enfo_facilities:
            yield facility

        enfo_fac_count = len(seen_ids) - prev_count
        logger.info("Unique ENFO facilities: %s", enfo_fac_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def _fetch_enfo_orders(self) -> list[dict]:
        """Fetch all enforcement orders from the ENFO REST API."""
        all_items: list[dict] = []
        page = 1

        while True:
            self._rate_limit()
            params = {
                "pageSize": str(_ENFO_PAGE_SIZE),
                "page": str(page),
                "Sort": "DateDesc",
                "Status": "All",
            }

            for attempt in range(3):
                try:
                    logger.info("Querying ENFO orders (page=%s)...", page)
                    resp = self._client.get(
                        _ENFO_URL, params=params, follow_redirects=True
                    )

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
                        logger.info("Timeout querying ENFO, retrying in %ss...", wait)
                        time.sleep(wait)
                    else:
                        raise
            else:
                break

            data = resp.json()
            items = data.get("items", [])
            all_items.extend(items)

            page_count = len(items)
            total = data.get("totalCount", 0)
            logger.info("Got %s orders (total so far: %s / %s)", page_count, len(all_items), total)

            if len(all_items) >= total or page_count < _ENFO_PAGE_SIZE:
                break

            page += 1

        logger.info("Total ENFO orders: %s", len(all_items))
        return all_items

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "GA":
            return

        enfo_items = self._enfo_items
        if enfo_items is None:
            enfo_items = self._fetch_enfo_orders()

        enfo_count = 0
        for item in enfo_items:
            try:
                violation = map_enforcement_violation(item)
                if violation.source_id:
                    yield violation
                    enfo_count += 1
            except Exception as e:
                logger.warning("Skipping ENFO violation: %s", e)

        logger.info("ENFO violations: %s", enfo_count)

