"""Connector for SC DES (South Carolina Dept of Environmental Services) data.

In 2024, South Carolina reorganized SCDHEC into two agencies. Environmental
programs now live under SC DES (Department of Environmental Services), and
the GIS endpoints moved from gis.dhec.sc.gov to gis.des.sc.gov.

Downloads JSON data from SC DES's ArcGIS REST services. Facility datasets:
  - NPDES Individual Permits (water/Water_Permits/MapServer/0)
  - Public Water Supply Wells (water/Water_PublicWaterSupply/MapServer/1) — URL
    not confirmed, best-guess based on the post-reorg naming pattern; may 404
  - Mines (water/Water_Permits/MapServer/16)
  - State Regulated Dams (water/Water_Permits/MapServer/18)

The BEHS_Complaints dataset (formerly under environment/BEHS_Complaints) was
retired with the ePermitting system; SC DES says complaints now live behind
per-program forms with no public GIS feed. Code path removed from this
connector.

This source only covers South Carolina (state="SC").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.sc_des_mapper import (
    map_dam,
    map_mine,
    map_npdes,
    map_pws_well,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API URLs (SC DES, post-2024 reorganization)
_PERMITS_BASE = "https://gis.des.sc.gov/gisserver/rest/services/water/Water_Permits/MapServer"
# NOTE: PWS URL unverified — contact's email duplicated the Permits URL where
# the PWS URL should have been. Best-guess pattern based on the Permits
# rename. Replace if 404.
_PWS_BASE = "https://gis.des.sc.gov/gisserver/rest/services/water/Water_PublicWaterSupply/MapServer"

_NPDES_URL = f"{_PERMITS_BASE}/0/query"
_PWS_URL = f"{_PWS_BASE}/1/query"
_MINES_URL = f"{_PERMITS_BASE}/16/query"
_DAMS_URL = f"{_PERMITS_BASE}/18/query"


class SCDESSource(ArcGISSource):
    """Connector for South Carolina DES ArcGIS REST services."""

    name = "sc_des"
    page_size = 3000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from SC DES NPDES, PWS, mines, and dams datasets.

        SC DES only covers South Carolina. Returns empty for non-SC states.
        """
        if state.upper() != "SC":
            logger.warning("Skipping %s (SC DES is South Carolina-only)", state)
            return

        seen_ids: set[str] = set()

        # NPDES Individual Permits
        npdes_features = self._query_features(_NPDES_URL, "NPDES Individual Permits")
        for feat in npdes_features:
            try:
                facility = map_npdes(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping NPDES permit: %s", e)

        npdes_count = len(seen_ids)
        logger.info("Unique NPDES permits: %s", npdes_count)

        # Public Water Supply Wells
        pws_features = self._query_features(_PWS_URL, "Public Water Supply Wells")
        for feat in pws_features:
            try:
                facility = map_pws_well(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping PWS well: %s", e)

        pws_count = len(seen_ids) - npdes_count
        logger.info("Unique PWS wells: %s", pws_count)

        # Mines
        mine_features = self._query_features(_MINES_URL, "Mines")
        for feat in mine_features:
            try:
                facility = map_mine(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping mine: %s", e)

        mine_count = len(seen_ids) - npdes_count - pws_count
        logger.info("Unique mines: %s", mine_count)

        # State Regulated Dams
        dam_features = self._query_features(_DAMS_URL, "State Regulated Dams")
        for feat in dam_features:
            try:
                facility = map_dam(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping dam: %s", e)

        dam_count = len(seen_ids) - npdes_count - pws_count - mine_count
        logger.info("Unique dams: %s", dam_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """SC DES does not currently publish a public GIS feed of violations.

        The BEHS_Complaints dataset that previously served this role was
        retired with the ePermitting system in the 2024 reorg. For SC
        violations, use federal EPA ECHO instead.
        """
        if state.upper() != "SC":
            return
        logger.info(
            "SC DES has no public GIS feed for violations post-2024 reorg; "
            "use federal EPA ECHO for SC violations"
        )
        return
        yield  # pragma: no cover  -- keeps generator signature
