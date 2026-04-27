"""Connector for OR DEQ (Oregon Department of Environmental Quality) data.

Downloads JSON data from OR DEQ's ArcGIS REST services at arcgis.deq.state.or.us.
Six facility datasets from the DrinkingWaterProtectionPCS MapServer:
  - ECSI Cleanup sites with known contamination (layer 2)
  - Hazardous Material Generator sites (layer 4)
  - Leaking Underground Storage Tanks (layer 9)
  - Solid Waste sites (layer 16)
  - Underground Storage Tanks (layer 18)
  - Water Quality active permits (layer 20)

All layers share the same field schema (COMMON_NM, Address, City, County, etc.).

Enforcement dataset from deq.state.or.us/programs/enforcement:
  HTML table via ASP POST, 6,222 enforcement records (1998–present).
  9 columns: Enforcement Number, Program, Region, Source Name,
  Source Location, Enforcement Type, Violations, Issued, Penalty.

This source only covers Oregon (state="OR").
"""

from __future__ import annotations

import re
import time
from typing import Iterator
import logging

import httpx

from ..models import Facility, Violation
from ..normalize.or_deq_mapper import (
    map_enforcement_facility,
    map_enforcement_violation,
    map_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://arcgis.deq.state.or.us/arcgis/rest/services/WQ/DrinkingWaterProtectionPCS/MapServer"

_LAYERS = [
    (2, "ECSI Cleanup Sites"),
    (4, "Hazardous Material Generators"),
    (9, "Leaking Underground Storage Tanks"),
    (16, "Solid Waste Sites"),
    (18, "Underground Storage Tanks"),
    (20, "Water Quality Active Permits"),
]

# Enforcement search ASP endpoint (POST with empty filters = all records)
_ENF_URL = "https://www.deq.state.or.us/programs/enforcement/EnfResults.asp"
_ENF_FORM_DATA = {
    "Program": "",
    "FacilityName": "",
    "CaseNumber": "",
    "Region": "",
    "EnforcementType": "",
    "County": "",
    "City": "",
    "ZipCode": "",
    "Submit": "Search",
}

# Regex to extract text from <font> tags within <td> cells
_FONT_RE = re.compile(r"<font[^>]*>(.*?)</font>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_DATA_ROW_RE = re.compile(
    r'<tr\s+bgcolor="#eeeeee">(.*?)</tr>', re.DOTALL
)

class ORDEQSource(ArcGISSource):
    """Connector for Oregon DEQ ArcGIS REST services."""

    name = "or_deq"
    page_size = 1000

    def __init__(self) -> None:
        super().__init__()
        self._enf_rows: list[dict] | None = None

    def _query_layer(self, layer_id: int, label: str) -> list[dict]:
        """Query an ArcGIS MapServer layer by layer ID."""
        url = f"{_BASE}/{layer_id}/query"
        return self._query_features(url, label)

    def _parse_enf_row(self, row_html: str) -> dict | None:
        """Parse a single enforcement HTML table row into a dict."""
        cells = _TD_RE.findall(row_html)
        if len(cells) < 9:
            return None

        def extract_text(cell_html: str) -> str:
            m = _FONT_RE.search(cell_html)
            if m:
                return m.group(1).strip()
            # Fallback: strip all tags
            return re.sub(r"<[^>]+>", "", cell_html).strip()

        return {
            "enf_number": extract_text(cells[0]),
            "program": extract_text(cells[1]),
            "region": extract_text(cells[2]),
            "source_name": extract_text(cells[3]),
            "location": extract_text(cells[4]),
            "enf_type": extract_text(cells[5]),
            "violations": extract_text(cells[6]),
            "issued": extract_text(cells[7]),
            "penalty": extract_text(cells[8]),
        }

    def _fetch_enforcement(self) -> list[dict]:
        """Fetch and parse the OR DEQ enforcement HTML table."""
        if self._enf_rows is not None:
            return self._enf_rows

        logger.info("Fetching enforcement data from ASP endpoint...")
        self._rate_limit()

        for attempt in range(3):
            try:
                resp = self._client.post(
                    _ENF_URL,
                    data=_ENF_FORM_DATA,
                    follow_redirects=True,
                )
                resp.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    logger.error("Error fetching enforcement: %s, retrying in %ss...", e, wait)
                    time.sleep(wait)
                else:
                    logger.error("Failed to fetch enforcement after 3 attempts: %s", e)
                    self._enf_rows = []
                    return []

        # Response is latin-1 encoded
        html = resp.content.decode("latin-1")

        # Parse all data rows
        rows = []
        for match in _DATA_ROW_RE.finditer(html):
            parsed = self._parse_enf_row(match.group(1))
            if parsed and parsed.get("enf_number"):
                rows.append(parsed)

        logger.info("Parsed %s enforcement records", len(rows))
        self._enf_rows = rows
        return rows

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "OR":
            logger.warning("Skipping %s (OR DEQ is Oregon-only)", state)
            return

        seen_ids: set[str] = set()

        for layer_id, label in _LAYERS:
            features = self._query_layer(layer_id, label)
            layer_count = 0
            for feat in features:
                try:
                    facility = map_facility(feat, label)
                    if facility.source_id and facility.source_id not in seen_ids:
                        seen_ids.add(facility.source_id)
                        yield facility
                        layer_count += 1
                except Exception as e:
                    logger.warning("Skipping %s feature: %s", label, e)

            logger.info("Unique %s: %s", label, layer_count)

        # Enforcement facilities (from HTML)
        enf_rows = self._fetch_enforcement()
        enf_count = 0
        for row in enf_rows:
            try:
                facility = map_enforcement_facility(row)
                if facility and facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    enf_count += 1
            except Exception as e:
                logger.warning("Skipping enforcement facility: %s", e)

        logger.info("Unique enforcement facilities: %s", enf_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "OR":
            logger.warning("Skipping %s (OR DEQ is Oregon-only)", state)
            return

        enf_rows = self._fetch_enforcement()
        enf_count = 0
        for row in enf_rows:
            try:
                violation = map_enforcement_violation(row)
                if violation and violation.source_id:
                    yield violation
                    enf_count += 1
            except Exception as e:
                logger.warning("Skipping enforcement violation: %s", e)

        logger.info("Enforcement violations: %s", enf_count)

