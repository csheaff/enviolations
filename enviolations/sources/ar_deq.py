"""Connector for AR DEQ (Arkansas Department of Environmental Quality) data.

Downloads JSON data from Arkansas GIS Environment FeatureServer at gis.arkansas.gov.
Two datasets:
  - FACILITIES_DEQ (FeatureServer/0): ~21,968 regulated facilities
  - Inspections (MapServer/3 at gis.adeq.state.ar.us): ~105K inspection records
    with compliance status

Note: The Arkansas FeatureServer has a very low MaxRecordCount (200), requiring
extensive pagination. The Inspections MapServer allows 1000 per page.

This source only covers Arkansas (state="AR").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ar_deq_mapper import has_violation, map_facility, map_inspection
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_FACILITIES_URL = "https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Environment/FeatureServer/0/query"
_INSPECTIONS_URL = "https://gis.adeq.state.ar.us/arcgis/rest/services/InspectionsComplaints/MapServer/3/query"

_INSPECTIONS_PAGE_SIZE = 1000

class ARDEQSource(ArcGISSource):
    """Connector for Arkansas DEQ ArcGIS FeatureServer."""

    name = "ar_deq"
    page_size = 200

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "AR":
            logger.warning("Skipping %s (AR DEQ is Arkansas-only)", state)
            return

        seen_ids: set[str] = set()

        # Facilities
        features = self._query_features(_FACILITIES_URL, "FACILITIES_DEQ")
        for feat in features:
            try:
                facility = map_facility(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping facility: %s", e)

        logger.info("Total unique facilities: %s", len(seen_ids))

    def _query_inspections(self, label: str) -> list[dict]:
        """Query the Inspections MapServer with pagination (1000/page)."""
        return self._query_features(
            _INSPECTIONS_URL, label,
            return_geometry=False, page_size=_INSPECTIONS_PAGE_SIZE,
        )

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "AR":
            logger.warning("Skipping %s (AR DEQ is Arkansas-only)", state)
            return

        features = self._query_inspections("Inspections")
        total = 0
        kept = 0
        for feat in features:
            total += 1
            if not has_violation(feat):
                continue
            try:
                yield map_inspection(feat)
                kept += 1
            except Exception as e:
                logger.warning("Skipping inspection: %s", e)

        logger.info("AR: %s actual violations from %s inspections", kept, total)

