"""Connector for MI EGLE PFAS data (Michigan MPART PFAS Open Data).

Downloads JSON data from MI EGLE's ArcGIS REST services at
gisagoegle.state.mi.us. Three layers from EGLE/PfasOpenData/MapServer:
  - Layer 0: Surface Water PFAS sampling (3,719 records)
  - Layer 1: Fish Tissue PFAS sampling (32,164 records)
  - Layer 3: Compliance Monitoring water systems (23,640 records)

Separate from mi_egle because the data structure is fundamentally different
(PFAS sampling data vs facility/contamination data). Records are aggregated
to site/station/system level.

This source only covers Michigan (state="MI").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.mi_pfas_mapper import (
    map_compliance_system,
    map_fish_tissue_station,
    map_surface_water_site,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_SURFACE_WATER_URL = (
    "https://gisagoegle.state.mi.us/arcgis/rest/services"
    "/EGLE/PfasOpenData/MapServer/0/query"
)
_FISH_TISSUE_URL = (
    "https://gisagoegle.state.mi.us/arcgis/rest/services"
    "/EGLE/PfasOpenData/MapServer/1/query"
)
_COMPLIANCE_URL = (
    "https://gisagoegle.state.mi.us/arcgis/rest/services"
    "/EGLE/PfasOpenData/MapServer/3/query"
)


class MIPFASSource(ArcGISSource):
    """Connector for MI EGLE PFAS ArcGIS REST services."""

    name = "mi_pfas"
    page_size = 2000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "MI":
            logger.warning("Skipping %s (MI PFAS is Michigan-only)", state)
            return

        seen_ids: set[str] = set()

        # Surface Water PFAS sites (aggregate by SiteCode)
        sw_features = self._query_features(
            _SURFACE_WATER_URL, "PFAS Surface Water"
        )
        for feat in sw_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_surface_water_site(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping surface water site: %s", e)

        sw_count = len(seen_ids)
        logger.info("Unique surface water sites: %s", sw_count)

        # Fish Tissue stations (aggregate by StationID)
        ft_features = self._query_features(
            _FISH_TISSUE_URL, "PFAS Fish Tissue"
        )
        for feat in ft_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_fish_tissue_station(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping fish tissue station: %s", e)

        ft_count = len(seen_ids) - sw_count
        logger.info("Unique fish tissue stations: %s", ft_count)

        # Compliance Monitoring water systems (aggregate by WSSN)
        cm_features = self._query_features(
            _COMPLIANCE_URL, "PFAS Compliance Monitoring",
            return_geometry=False,
        )
        for feat in cm_features:
            attrs = feat.get("attributes", feat)
            try:
                facility = map_compliance_system(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping compliance system: %s", e)

        cm_count = len(seen_ids) - sw_count - ft_count
        logger.info("Unique compliance systems: %s", cm_count)
        logger.info("Total unique PFAS facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "MI":
            return
        return
        yield  # noqa: unreachable — makes this a generator
