"""Connector for ID DEQ (Idaho Department of Environmental Quality) data.

Downloads JSON data from Idaho DEQ's ArcGIS MapServer at mapcase.deq.idaho.gov.
One merged multi-agency dataset:
  - Potential Contaminant Inventory (PCI, Layer 12 on SWA_PCI_WMS): ~67K facilities
    covering UST, RCRA, NPDES, Superfund, Brownfield, TRI, feedlots, mines, etc.

Idaho DEQ does not publish structured violation/enforcement data via ArcGIS;
violations for Idaho are covered by federal EPA ECHO.

This source only covers Idaho (state="ID").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.id_deq_mapper import map_pci_facility
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API — PCI layer on SWA_PCI_WMS MapServer
_PCI_URL = (
    "https://mapcase.deq.idaho.gov/arcgis/rest/services"
    "/SWA_PCI_WMS/MapServer/12/query"
)

class IDDEQSource(ArcGISSource):
    """Connector for Idaho DEQ ArcGIS MapServer."""

    name = "id_deq"
    page_size = 1000

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "ID":
            logger.warning("Skipping %s (ID DEQ is Idaho-only)", state)
            return

        seen_ids: set[str] = set()

        features = self._query_features(_PCI_URL, "PCI Facilities")
        for feat in features:
            try:
                attrs = feat.get("attributes", feat)
                facility = map_pci_facility(attrs)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping PCI facility: %s", e)

        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "ID":
            logger.warning("Skipping %s (ID DEQ is Idaho-only)", state)
            return

        logger.warning("No state-level violation dataset available; " "use federal ECHO source for Idaho violations")
        return
        yield  # make this a generator

