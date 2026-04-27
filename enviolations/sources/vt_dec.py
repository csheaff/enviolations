"""Connector for VT DEC (Vermont Department of Environmental Conservation) data.

Downloads JSON data from Vermont ANR's ArcGIS MapServer at anrmaps.vermont.gov.
Four datasets:
  - Hazardous Waste Sites (ENVIRON Layer 163): ~5.2K cleanup/contaminated sites
  - Hazardous Waste Generators (ENVIRON Layer 160): ~2.9K RCRA generators
  - Underground Storage Tanks (FACILITIES Layer 162): ~2.6K UST records (dedup by FacilityID)
  - Landfills (ENVIRON Layer 164): ~334 landfill sites

Vermont DEC does not publish structured violation/enforcement data via ArcGIS;
violations for Vermont are covered by federal EPA ECHO.

This source only covers Vermont (state="VT").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.vt_dec_mapper import (
    map_haz_waste_site,
    map_haz_waste_generator,
    map_ust,
    map_landfill,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_ENVIRON_BASE = (
    "https://anrmaps.vermont.gov/arcgis/rest/services"
    "/Open_Data/OPENDATA_ANR_ENVIRON_SP_NOCACHE_v2/MapServer"
)
_FACILITIES_BASE = (
    "https://anrmaps.vermont.gov/arcgis/rest/services"
    "/Open_Data/OPENDATA_ANR_FACILITIES_SP_NOCACHE_v2/MapServer"
)

_HAZ_SITES_URL = f"{_ENVIRON_BASE}/163/query"
_HAZ_GENERATORS_URL = f"{_ENVIRON_BASE}/160/query"
_UST_URL = f"{_FACILITIES_BASE}/162/query"
_LANDFILLS_URL = f"{_ENVIRON_BASE}/164/query"

class VTDECSource(ArcGISSource):
    """Connector for Vermont DEC ArcGIS MapServer."""

    name = "vt_dec"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "VT":
            logger.warning("Skipping %s (VT DEC is Vermont-only)", state)
            return

        seen_ids: set[str] = set()

        # Hazardous Waste Sites (has LatY/LongX fields)
        haz_features = self._query_features(_HAZ_SITES_URL, "Hazardous Waste Sites")
        for feat in haz_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_haz_waste_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping haz waste site: %s", e)

        haz_count = len(seen_ids)
        logger.info("Unique hazardous waste sites: %s", haz_count)

        # Hazardous Waste Generators (need geometry for coords)
        gen_features = self._query_features(
            _HAZ_GENERATORS_URL, "Hazardous Waste Generators",
            return_geometry=True
        )
        for feat in gen_features:
            try:
                facility = map_haz_waste_generator(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping haz waste generator: %s", e)

        gen_count = len(seen_ids) - haz_count
        logger.info("Unique hazardous waste generators: %s", gen_count)

        # UST (has DecLat/DecLong fields; dedup by FacilityID)
        ust_features = self._query_features(_UST_URL, "Underground Storage Tanks")
        for feat in ust_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_ust(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping UST: %s", e)

        ust_count = len(seen_ids) - haz_count - gen_count
        logger.info("Unique UST facilities: %s", ust_count)

        # Landfills (has LATITUDE/LONGITUDE fields)
        lf_features = self._query_features(_LANDFILLS_URL, "Landfills")
        for feat in lf_features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_landfill(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping landfill: %s", e)

        lf_count = len(seen_ids) - haz_count - gen_count - ust_count
        logger.info("Unique landfills: %s", lf_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "VT":
            logger.warning("Skipping %s (VT DEC is Vermont-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Vermont violations")
        return
        yield  # make this a generator

