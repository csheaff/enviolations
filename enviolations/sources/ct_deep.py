"""Connector for CT DEEP (Connecticut Dept of Energy and Environmental Protection) data.

Downloads JSON data from CT DEEP's Socrata open data portal at data.ct.gov.
Three datasets:
  - UST Facility & Tank Details (utni-rddb): ~49K tank records, deduped to ~12K facilities
  - Contaminated Sites / Remediation (u76p-weqj): ~11.7K remediation sites
  - Formal Enforcement Case Summaries (t2bf-45ba): ~210 enforcement cases (2021-present)

This source only covers Connecticut (state="CT").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.ct_deep_mapper import (
    map_enforcement_facility,
    map_enforcement_violation,
    map_remediation_site,
    map_ust_facility,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

_BASE = "https://data.ct.gov/resource"
_UST_DATASET = "utni-rddb"
_REMEDIATION_DATASET = "u76p-weqj"
_ENFORCEMENT_DATASET = "t2bf-45ba"

_PAGE_SIZE = 5000

class CTDEEPSource(ArcGISSource):
    """Connector for Connecticut DEEP Socrata open data."""

    name = "ct_deep"
    timeout = 120.0
    rate_limit_delay = 0.5

    def _fetch_socrata(self, dataset_id: str, label: str) -> list[dict]:
        """Fetch all records from a Socrata SODA JSON endpoint with pagination."""
        url = f"{_BASE}/{dataset_id}.json"
        return self._query_socrata(url, label, page_size=_PAGE_SIZE)

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        if state.upper() != "CT":
            logger.warning("Skipping %s (CT DEEP is Connecticut-only)", state)
            return

        seen_ids: set[str] = set()

        # UST Facility & Tank Details — dedup by agencyfacilityid
        ust_records = self._fetch_socrata(_UST_DATASET, "UST Facility & Tank Details")
        seen_fac_ids: set[str] = set()
        ust_count = 0
        for rec in ust_records:
            try:
                fac_id = (rec.get("agencyfacilityid") or "").strip()
                if not fac_id or fac_id in seen_fac_ids:
                    continue
                seen_fac_ids.add(fac_id)

                facility = map_ust_facility(rec)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    ust_count += 1
            except Exception as e:
                logger.warning("Skipping UST record: %s", e)

        logger.info("Unique UST facilities: %s", ust_count)

        # Contaminated Sites / Remediation
        rem_records = self._fetch_socrata(_REMEDIATION_DATASET, "Remediation Sites")
        rem_count = 0
        for rec in rem_records:
            try:
                facility = map_remediation_site(rec)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    rem_count += 1
            except Exception as e:
                logger.warning("Skipping remediation site: %s", e)

        logger.info("Unique remediation sites: %s", rem_count)

        # Enforcement Case Summaries — create facility for each respondent
        enf_records = self._fetch_socrata(_ENFORCEMENT_DATASET, "Enforcement Cases")
        enf_count = 0
        for rec in enf_records:
            try:
                facility = map_enforcement_facility(rec)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    enf_count += 1
            except Exception as e:
                logger.warning("Skipping enforcement facility: %s", e)

        logger.info("Unique enforcement facilities: %s", enf_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        if state.upper() != "CT":
            logger.warning("Skipping %s (CT DEEP is Connecticut-only)", state)
            return

        # Formal Enforcement Case Summaries (2021-present)
        enf_records = self._fetch_socrata(_ENFORCEMENT_DATASET, "Enforcement Cases")
        enf_count = 0
        for rec in enf_records:
            try:
                violation = map_enforcement_violation(rec)
                yield violation
                enf_count += 1
            except Exception as e:
                logger.warning("Skipping enforcement violation: %s", e)

        logger.info("Enforcement violations: %s", enf_count)
