"""Connector for FL DEP Brownfield Sites and PFAS Cleanup Sites.

Downloads JSON data from FL DEP's ArcGIS REST services at ca.dep.state.fl.us.
Two datasets:
  - BROWNFIELD_AREAS/MapServer/1: Brownfield Sites (~533) → facilities
  - CLEANUP_SP/MapServer/4: ERIC PFAS Sites (~249) → facilities

This source only covers Florida (state="FL").
No violations are available — facilities only.

Geocoding
---------
The BROWNFIELD_AREAS layer returns polygon geometry (rings, not point x/y),
so ArcGIS geometry coordinates are unavailable.  The layer's attribute fields
(LATITUDE/LONGITUDE, DMS LAT_DD/LAT_MM/LAT_SS) are also absent or null for
most records.  As a result, all brownfield sites lack coordinates after the
mapper runs.

After mapping, any facility with null lat/lon but a usable address is batch-
geocoded via the Census Bureau batch API (batch_geocode_addresses).  This
gives brownfield sites a geographic anchor so they appear in radius searches.
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..geo import batch_geocode_addresses
from ..models import Facility, Violation
from ..normalize.fl_dep_bf_mapper import map_brownfield_site, map_pfas_site
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints
_BROWNFIELD_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/BROWNFIELD_AREAS/MapServer/1/query"
)
_PFAS_URL = (
    "https://ca.dep.state.fl.us/arcgis/rest/services"
    "/OpenData/CLEANUP_SP/MapServer/4/query"
)


def _geocode_facilities(facilities: list[Facility], label: str) -> None:
    """Batch geocode facilities that have null lat/lon but a valid address.

    Mutates facilities in place.  Uses Census Bureau batch geocoder.
    Facilities with no address or that fail geocoding are left with null coords.
    """
    needs_geocode = [
        f for f in facilities
        if f.lat is None and f.address and f.city
    ]
    if not needs_geocode:
        return

    records = [
        (
            f.source_id,
            f.address or "",
            f.city or "",
            f.state or "FL",
            f.zip_code or "",
        )
        for f in needs_geocode
    ]
    coords = batch_geocode_addresses(records)

    geocoded = 0
    for fac in needs_geocode:
        if fac.source_id in coords:
            fac.lat, fac.lon = coords[fac.source_id]
            geocoded += 1

    logger.info(
        "%s geocoding: %d/%d facilities received coordinates",
        label, geocoded, len(needs_geocode),
    )


class FLDEPBFSource(ArcGISSource):
    """Connector for FL DEP Brownfield Sites and PFAS Cleanup Sites."""

    name = "fl_dep_bf"
    page_size = 1000

    @staticmethod
    def _has_id(source_id: str, prefix: str) -> bool:
        """True if source_id has a real ID after the prefix."""
        return bool(source_id) and source_id != prefix

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from Brownfield Sites and PFAS Cleanup Sites.

        FL DEP Brownfields/PFAS only covers Florida. Returns empty for
        non-FL states.

        Brownfield sites lack point geometry (polygon layer), so facilities
        with null coordinates are batch-geocoded via the Census Bureau API
        before yielding.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP BF is Florida-only)", state)
            return

        seen_ids: set[str] = set()

        # Brownfield Sites — collect first so we can batch-geocode
        bf_facilities: list[Facility] = []
        bf_features = self._query_features(
            _BROWNFIELD_URL, "Brownfield Sites", return_geometry=True
        )
        for feat in bf_features:
            try:
                facility = map_brownfield_site(feat)
                if (
                    self._has_id(facility.source_id, "bf-")
                    and facility.source_id not in seen_ids
                ):
                    seen_ids.add(facility.source_id)
                    bf_facilities.append(facility)
            except Exception as e:
                logger.warning("Skipping brownfield site: %s", e)

        _geocode_facilities(bf_facilities, "Brownfield Sites")

        for facility in bf_facilities:
            yield facility

        bf_count = len(seen_ids)
        logger.info("Unique brownfield sites: %s", bf_count)

        # PFAS Cleanup Sites — collect and batch-geocode missing coords
        pfas_facilities: list[Facility] = []
        pfas_features = self._query_features(
            _PFAS_URL, "PFAS Cleanup Sites", return_geometry=True
        )
        for feat in pfas_features:
            try:
                facility = map_pfas_site(feat)
                if (
                    self._has_id(facility.source_id, "pfas-")
                    and facility.source_id not in seen_ids
                ):
                    seen_ids.add(facility.source_id)
                    pfas_facilities.append(facility)
            except Exception as e:
                logger.warning("Skipping PFAS site: %s", e)

        _geocode_facilities(pfas_facilities, "PFAS Cleanup Sites")

        for facility in pfas_facilities:
            yield facility

        pfas_count = len(seen_ids) - bf_count
        logger.info("Unique PFAS sites: %s", pfas_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """No violation data available. Returns empty iterator.

        FL DEP Brownfields/PFAS only covers Florida. Returns empty for
        non-FL states.
        """
        if state.upper() != "FL":
            logger.warning("Skipping %s (FL DEP BF is Florida-only)", state)
        # No violations available — contamination/cleanup sites only
        return
        yield  # makes this a generator returning empty iterator
