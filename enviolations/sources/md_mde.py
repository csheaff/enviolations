"""Connector for MD MDE (Maryland Department of the Environment) data.

Downloads JSON data from MD MDE's ArcGIS REST services at
mdgeodata.md.gov and mde.geodata.md.gov.
Three facility datasets:
  - Significant Wastewater Treatment Plants
    (Environment/MD_PointSourceDischarges/MapServer/0)
  - Point Source Discharges
    (Environment/MD_PointSourceDischarges/MapServer/1)
  - BioSolid Permits
    (LMA_Resource_Management_Program/BioSolid_Permits_Current_and_Historical/MapServer/1)

Violation/enforcement data from Socrata (opendata.maryland.gov):
  - WSA Violations (jwx7-mgcz) — ~2.7K water/sewer violations
  - WSA Enforcement Actions (qbwh-5vec) — ~1.2K enforcement orders/penalties
  - ARA Enforcement Actions (fpps-g5hi) — ~100 air quality enforcement

This source only covers Maryland (state="MD").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.md_mde_mapper import (
    map_ara_enforcement,
    map_ara_facility,
    map_biosolid,
    map_point_source,
    map_wsa_enforcement,
    map_wsa_facility,
    map_wsa_violation,
    map_wwtp,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API URLs
_WWTP_URL = "https://mdgeodata.md.gov/imap/rest/services/Environment/MD_PointSourceDischarges/MapServer/0/query"
_POINT_SOURCE_URL = "https://mdgeodata.md.gov/imap/rest/services/Environment/MD_PointSourceDischarges/MapServer/1/query"
_BIOSOLID_URL = "https://mde.geodata.md.gov/mdedata/rest/services/LMA_Resource_Management_Program/BioSolid_Permits_Current_and_Historical/MapServer/1/query"

# Socrata datasets on opendata.maryland.gov
_SOCRATA_BASE = "https://opendata.maryland.gov/resource"
_WSA_VIOLATIONS = "jwx7-mgcz"   # WSA Violations (~2.7K)
_WSA_ENFORCEMENT = "qbwh-5vec"  # WSA Enforcement Actions (~1.2K)
_ARA_ENFORCEMENT = "fpps-g5hi"  # ARA Air Enforcement (~100)
_SOCRATA_PAGE_SIZE = 5000

class MDMDESource(ArcGISSource):
    """Connector for Maryland MDE ArcGIS REST services."""

    name = "md_mde"
    page_size = 950

    def _fetch_socrata(self, dataset_id: str, label: str) -> list[dict]:
        """Fetch all records from a Socrata SODA JSON endpoint with pagination."""
        url = f"{_SOCRATA_BASE}/{dataset_id}.json"
        return self._query_socrata(url, label, page_size=_SOCRATA_PAGE_SIZE)

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from MD MDE WWTP, point source, and biosolid datasets.

        MD MDE only covers Maryland. Returns empty for non-MD states.
        """
        if state.upper() != "MD":
            logger.warning("Skipping %s (MD MDE is Maryland-only)", state)
            return

        seen_ids: set[str] = set()

        # Significant WWTPs
        wwtp_features = self._query_features(_WWTP_URL, "Significant WWTPs")
        for feat in wwtp_features:
            try:
                facility = map_wwtp(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping WWTP: %s", e)

        wwtp_count = len(seen_ids)
        logger.info("Unique WWTPs: %s", wwtp_count)

        # Point Source Discharges
        ps_features = self._query_features(_POINT_SOURCE_URL, "Point Source Discharges")
        for feat in ps_features:
            try:
                facility = map_point_source(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping point source: %s", e)

        ps_count = len(seen_ids) - wwtp_count
        logger.info("Unique point sources: %s", ps_count)

        # BioSolid Permits
        bio_features = self._query_features(_BIOSOLID_URL, "BioSolid Permits")
        for feat in bio_features:
            try:
                facility = map_biosolid(feat)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping biosolid permit: %s", e)

        bio_count = len(seen_ids) - wwtp_count - ps_count
        logger.info("Unique biosolid permits: %s", bio_count)

        # WSA facilities from violation/enforcement data
        wsa_vio_records = self._fetch_socrata(_WSA_VIOLATIONS, "WSA Violations")
        wsa_enf_records = self._fetch_socrata(_WSA_ENFORCEMENT, "WSA Enforcement")
        self._wsa_vio_cache = wsa_vio_records
        self._wsa_enf_cache = wsa_enf_records

        wsa_count = 0
        for rec in wsa_vio_records + wsa_enf_records:
            try:
                facility = map_wsa_facility(rec)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    wsa_count += 1
            except Exception as e:
                logger.warning("Skipping WSA facility: %s", e)
        logger.info("Unique WSA facilities: %s", wsa_count)

        # ARA facilities from air enforcement data
        ara_records = self._fetch_socrata(_ARA_ENFORCEMENT, "ARA Air Enforcement")
        self._ara_cache = ara_records

        ara_count = 0
        for rec in ara_records:
            try:
                facility = map_ara_facility(rec)
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    ara_count += 1
            except Exception as e:
                logger.warning("Skipping ARA facility: %s", e)
        logger.info("Unique ARA facilities: %s", ara_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from MD MDE Socrata enforcement datasets."""
        if state.upper() != "MD":
            logger.warning("Skipping %s (MD MDE is Maryland-only)", state)
            return

        # Use cached data from fetch_facilities if available, else fetch fresh
        wsa_vio_records = getattr(self, "_wsa_vio_cache", None)
        if wsa_vio_records is None:
            wsa_vio_records = self._fetch_socrata(_WSA_VIOLATIONS, "WSA Violations")

        wsa_enf_records = getattr(self, "_wsa_enf_cache", None)
        if wsa_enf_records is None:
            wsa_enf_records = self._fetch_socrata(_WSA_ENFORCEMENT, "WSA Enforcement")

        ara_records = getattr(self, "_ara_cache", None)
        if ara_records is None:
            ara_records = self._fetch_socrata(_ARA_ENFORCEMENT, "ARA Air Enforcement")

        # WSA Violations
        seen_vio_ids: set[str] = set()
        vio_count = 0
        for rec in wsa_vio_records:
            try:
                violation = map_wsa_violation(rec)
                if violation.source_id not in seen_vio_ids:
                    seen_vio_ids.add(violation.source_id)
                    yield violation
                    vio_count += 1
            except Exception as e:
                logger.warning("Skipping WSA violation: %s", e)
        logger.info("WSA violations: %s", vio_count)

        # WSA Enforcement Actions
        enf_count = 0
        for rec in wsa_enf_records:
            try:
                violation = map_wsa_enforcement(rec)
                if violation.source_id not in seen_vio_ids:
                    seen_vio_ids.add(violation.source_id)
                    yield violation
                    enf_count += 1
            except Exception as e:
                logger.warning("Skipping WSA enforcement: %s", e)
        logger.info("WSA enforcement actions: %s", enf_count)

        # ARA Air Enforcement
        ara_count = 0
        for rec in ara_records:
            try:
                violation = map_ara_enforcement(rec)
                if violation.source_id not in seen_vio_ids:
                    seen_vio_ids.add(violation.source_id)
                    yield violation
                    ara_count += 1
            except Exception as e:
                logger.warning("Skipping ARA enforcement: %s", e)
        logger.info("ARA air enforcement: %s", ara_count)
        logger.info("Total violations: %s", vio_count + enf_count + ara_count)

