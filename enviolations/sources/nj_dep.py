"""Connector for NJ DEP (New Jersey Dept. of Environmental Protection).

Downloads JSON data from NJ DEP's ArcGIS REST MapServer. Five datasets
from the Environmental_NJEMS service:
  - Known Contaminated Sites List (Layer 0) → facilities
  - NJEMS Sites (Layer 2) → facilities
  - Underground Storage Tanks (Layer 9) → UST site ID set
  - NJDEP Air Quality Permitted Facilities (Layer 15) → pref_id lookup
  - Enforcement Actions (Layer 18) → violations

The Air Quality layer (Layer 15) is used only to build a lookup table
(PREF_ID_NUM → NJEMS SITE_ID) that resolves non-numeric enforcement
PREF_ID_NUMs (A/H-prefix Air Quality identifiers) to the correct NJEMS
facility record. Without this lookup, violations from Air Quality-regulated
facilities would be attached to orphan kcsl-{pref_id} stubs and invisible
to entity resolution across sources.

The Underground Storage Tanks layer (Layer 9) is used to build a set of
NJEMS SITE_IDs with active UST registrations. These SITE_IDs are passed
to map_njems_site() so that UST sites receive programs="NJEMS, UST" and
appear correctly in the LUST/UST program filter (CIV-510).

This source only covers New Jersey (state="NJ").
"""

from __future__ import annotations

from typing import Iterator
import logging

from ..models import Facility, Violation
from ..normalize.nj_dep_mapper import (
    map_enforcement_action,
    map_enforcement_facility,
    map_kcsl_site,
    map_njems_site,
)
from .arcgis_base import ArcGISSource

logger = logging.getLogger(__name__)

# ArcGIS REST API endpoints (MapServer layers)
_KCSL_URL = (
    "https://mapsdep.nj.gov/arcgis/rest/services"
    "/Features/Environmental_NJEMS/MapServer/0/query"
)
_NJEMS_URL = (
    "https://mapsdep.nj.gov/arcgis/rest/services"
    "/Features/Environmental_NJEMS/MapServer/2/query"
)
_UST_URL = (
    "https://mapsdep.nj.gov/arcgis/rest/services"
    "/Features/Environmental_NJEMS/MapServer/9/query"
)
_AIR_QUALITY_URL = (
    "https://mapsdep.nj.gov/arcgis/rest/services"
    "/Features/Environmental_NJEMS/MapServer/15/query"
)
_ENFORCEMENT_URL = (
    "https://mapsdep.nj.gov/arcgis/rest/services"
    "/Features/Environmental_NJEMS/MapServer/18/query"
)

class NJDEPSource(ArcGISSource):
    """Connector for NJ DEP ArcGIS REST MapServer."""

    name = "nj_dep"

    def __init__(self) -> None:
        super().__init__()
        self._enforcement_features: list[dict] | None = None
        self._pref_id_lookup: dict[str, int] | None = None
        self._ust_site_ids: set[int] | None = None

    def _fetch_enforcement(self) -> list[dict]:
        """Download and cache enforcement features for reuse."""
        if self._enforcement_features is not None:
            return self._enforcement_features
        self._enforcement_features = self._query_features(
            _ENFORCEMENT_URL, "Enforcement Actions",
        )
        return self._enforcement_features

    def _build_pref_id_lookup(self) -> dict[str, int]:
        """Build a lookup table from PREF_ID_NUM to NJEMS SITE_ID.

        NJ DEP enforcement actions (Layer 18) all belong to the Air Quality
        program. Their PREF_ID_NUM values are Air Quality permit identifiers,
        not NJEMS SITE_IDs. The Air Quality Permitted Facilities layer
        (Layer 15) provides the authoritative mapping from PREF_ID_NUM to
        the correct NJEMS SITE_ID.

        Critically, numeric PREF_ID_NUMs are NOT the same as NJEMS SITE_IDs,
        even though they look similar. For example, PREF_ID_NUM "41955"
        resolves to NJEMS SITE_ID 8616 (Evergreen Cemetery & Crematory, Newark),
        not to SITE_ID 41955 (Liquid Carbonic Corp, Harrison). Treating numeric
        PREF_IDs as direct NJEMS SITE_IDs causes violations to be attributed
        to the wrong facility.

        Returns dict mapping PREF_ID_NUM string → SITE_ID int.
        Includes all entries from Layer 15 (both numeric and non-numeric).
        """
        if self._pref_id_lookup is not None:
            return self._pref_id_lookup

        features = self._query_features(
            _AIR_QUALITY_URL,
            "Air Quality Facilities (pref_id lookup)",
            return_geometry=False,
        )
        lookup: dict[str, int] = {}
        for feat in features:
            attrs = feat.get("attributes", {})
            pref_id = attrs.get("PREF_ID_NUM")
            site_id = attrs.get("SITE_ID")
            if pref_id and site_id is not None:
                pref_id_str = str(pref_id).strip()
                try:
                    lookup[pref_id_str] = int(site_id)
                except (ValueError, TypeError):
                    pass

        self._pref_id_lookup = lookup
        logger.info("Built Air Quality pref_id lookup: %s entries", len(lookup))
        return lookup

    def _build_ust_site_ids(self) -> set[int]:
        """Build a set of NJEMS SITE_IDs with active UST registrations.

        Queries Layer 9 (Underground Storage Tanks) for non-terminated
        registrations and returns the set of SITE_IDs. These are cross-
        referenced with NJEMS Sites (Layer 2) in fetch_facilities() so that
        UST sites receive programs="NJEMS, UST" and appear in the LUST/UST
        program filter (CIV-510).

        Terminated registrations (DOC_STATUS='Terminated') are excluded because
        they represent removed or permanently closed tanks that no longer pose
        active UST risk. Active registrations include DOC_STATUS values:
          - "Effective" — current valid registration
          - "Inspection Conducted" — inspection performed, registration active

        Returns set of NJEMS SITE_ID integers.
        """
        if self._ust_site_ids is not None:
            return self._ust_site_ids

        features = self._query_features(
            _UST_URL,
            "Underground Storage Tanks (UST site IDs)",
            where="DOC_STATUS <> 'Terminated'",
            return_geometry=False,
        )
        site_ids: set[int] = set()
        for feat in features:
            attrs = feat.get("attributes", {})
            site_id = attrs.get("SITE_ID")
            if site_id is not None:
                try:
                    site_ids.add(int(site_id))
                except (ValueError, TypeError):
                    pass

        self._ust_site_ids = site_ids
        logger.info("Built UST site ID set: %s unique sites", len(site_ids))
        return site_ids

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield facilities from NJ DEP KCSL and NJEMS Sites.

        NJ DEP only covers New Jersey. Returns empty for non-NJ states.
        """
        if state.upper() != "NJ":
            logger.warning("Skipping %s (NJ DEP is New Jersey-only)", state)
            return

        seen_ids: set[str] = set()

        # Known Contaminated Sites List
        kcsl_features = self._query_features(_KCSL_URL, "Known Contaminated Sites")
        for feat in kcsl_features:
            try:
                facility = map_kcsl_site(feat["attributes"], feat.get("geometry"))
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping KCSL site: %s", e)

        kcsl_count = len(seen_ids)
        logger.info("Unique KCSL sites: %s", kcsl_count)

        # Build UST site ID set before processing NJEMS Sites so that UST-enrolled
        # sites receive programs="NJEMS, UST" and appear in the LUST/UST filter.
        ust_site_ids = self._build_ust_site_ids()

        # NJEMS Sites
        njems_features = self._query_features(_NJEMS_URL, "NJEMS Sites")
        for feat in njems_features:
            try:
                facility = map_njems_site(
                    feat["attributes"], feat.get("geometry"),
                    ust_site_ids=ust_site_ids,
                )
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
            except Exception as e:
                logger.warning("Skipping NJEMS site: %s", e)

        njems_count = len(seen_ids) - kcsl_count
        logger.info("Unique NJEMS sites: %s", njems_count)

        # Build pref_id lookup before processing enforcement actions so that
        # Air Quality facility stubs use njems-{SITE_ID} instead of kcsl-{pref_id}.
        pref_id_lookup = self._build_pref_id_lookup()

        # Create facilities from enforcement actions for PREF_ID_NUM linkage
        enf_features = self._fetch_enforcement()
        enf_fac_count = 0
        for feat in enf_features:
            try:
                facility = map_enforcement_facility(
                    feat["attributes"], pref_id_lookup=pref_id_lookup
                )
                if facility.source_id and facility.source_id not in seen_ids:
                    seen_ids.add(facility.source_id)
                    yield facility
                    enf_fac_count += 1
            except Exception:
                pass
        logger.info("Enforcement-derived facilities: %s", enf_fac_count)
        logger.info("Total unique facilities: %s", len(seen_ids))

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations from NJ DEP Enforcement Actions (Layer 18).

        Returns empty for non-NJ states.
        """
        if state.upper() != "NJ":
            logger.warning("Skipping %s (NJ DEP is New Jersey-only)", state)
            return

        # Build pref_id lookup so Air Quality violations link to the correct
        # njems-{SITE_ID} facility rather than an orphan kcsl- stub.
        pref_id_lookup = self._build_pref_id_lookup()

        enf_features = self._fetch_enforcement()
        count = 0
        for feat in enf_features:
            try:
                violation = map_enforcement_action(
                    feat["attributes"], pref_id_lookup=pref_id_lookup
                )
                yield violation
                count += 1
            except Exception as e:
                logger.warning("Skipping enforcement action: %s", e)

        logger.info("Total enforcement actions yielded: %s", count)

