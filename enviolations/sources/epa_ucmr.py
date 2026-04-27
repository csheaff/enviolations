"""Connector for EPA UCMR 5 (Unregulated Contaminant Monitoring Rule) bulk data.

Downloads PFAS drinking water monitoring results from ~9,240 public water
systems across all 50 states. Data is bulk tab-delimited text from:
https://www.epa.gov/dwucmr/occurrence-data-unregulated-contaminant-monitoring-rule

This is a federal source covering all 50 states + DC.

The UCMR 5 occurrence data file contains sample-level results (one row per
compound per sampling point per collection date). This connector downloads
the bulk file, parses it, filters to the requested state, and aggregates
to facility-level records via the mapper.

No API key required -- public bulk download.
"""

from __future__ import annotations

import csv
import io
import logging
import sqlite3
import zipfile
from typing import Iterator

import httpx

from ..models import Facility, Violation
from ..normalize.epa_ucmr_mapper import aggregate_pws_facilities, aggregate_pws_violations
from .base import DataSource

logger = logging.getLogger(__name__)


def enrich_ucmr_coordinates(conn: sqlite3.Connection, state: str | None = None) -> int:
    """Enrich UCMR facilities with coordinates via ZIP centroid geocoding.

    SDWA records use EPA Registry IDs as source_id (not PWSIDs), so a direct
    join on source_id is not viable. Instead, use the ZIP code from the UCMR
    bulk data to look up ZIP centroids computed from other already-geocoded
    facilities in the DB. Falls back to the Census geocoder API for ZIP codes
    not covered by the in-DB average.

    State is already extracted from the PWSID prefix in the mapper.

    Returns count of facilities updated.
    """
    from ..geo import zip_centroid

    # Two separate WHERE snippets: one for the aliased JOIN query, one for the plain query.
    where_state_join = "AND u.state = ?" if state else ""
    where_state_plain = "AND state = ?" if state else ""
    params: list = [state] if state else []

    # Step 1: use SQL join to assign zip centroids from already-geocoded
    # facilities in the DB.  This is fast, offline, and handles the bulk
    # of cases where other sources (ECHO, SDWA, TCEQ...) cover the same
    # zip code.
    zip_join_sql = f"""
        SELECT u.source_id,
               zc.avg_lat,
               zc.avg_lon
        FROM facilities u
        JOIN (
            SELECT SUBSTR(zip_code, 1, 5) AS zip5,
                   AVG(lat)               AS avg_lat,
                   AVG(lon)               AS avg_lon
            FROM facilities
            WHERE lat IS NOT NULL
              AND lon IS NOT NULL
              AND zip_code IS NOT NULL
              AND LENGTH(zip_code) >= 5
            GROUP BY zip5
        ) zc ON SUBSTR(u.zip_code, 1, 5) = zc.zip5
        WHERE u.source = 'epa_ucmr'
          AND u.lat IS NULL
          AND u.zip_code IS NOT NULL
          {where_state_join}
    """
    join_rows = conn.execute(zip_join_sql, params).fetchall()

    updated = 0
    if join_rows:
        updates = [
            (row[1], row[2], row[0])
            for row in join_rows
            if row[1] is not None and row[2] is not None
        ]
        conn.executemany(
            "UPDATE facilities SET lat = ?, lon = ? WHERE source = 'epa_ucmr' AND source_id = ?",
            updates,
        )
        conn.commit()
        updated += len(updates)
        logger.info(
            "Enriched %d UCMR facilities via zip-centroid DB join%s",
            len(updates),
            f" ({state})" if state else "",
        )

    # Step 2: Census API fallback for any remaining UCMR facilities that have
    # a ZIP code but whose ZIP wasn't in the DB average (e.g. very rural ZIPs
    # covered only by UCMR itself).
    remaining_rows = conn.execute(
        f"""
        SELECT source_id, zip_code
        FROM facilities
        WHERE source = 'epa_ucmr'
          AND lat IS NULL
          AND zip_code IS NOT NULL
          {where_state_plain}
        """,
        params,
    ).fetchall()

    for row in remaining_rows:
        pwsid = row[0]
        zip_code = row[1]

        centroid = zip_centroid(zip_code)
        if centroid:
            lat, lon = centroid
            conn.execute(
                "UPDATE facilities SET lat = ?, lon = ? "
                "WHERE source = 'epa_ucmr' AND source_id = ?",
                (lat, lon, pwsid),
            )
            updated += 1

    if remaining_rows:
        conn.commit()

    logger.info(
        "enrich_ucmr_coordinates: %d total UCMR facilities enriched%s",
        updated,
        f" ({state})" if state else "",
    )
    return updated

# UCMR 5 occurrence data bulk download URL.
# EPA publishes this as a zip containing tab-delimited text files.
# The URL may need updating if EPA restructures the page.
UCMR5_OCCURRENCE_URL = (
    "https://www.epa.gov/system/files/other-files/2023-08/"
    "ucmr5-occurrence-data.zip"
)

# Fallback: direct TSV URL if zip is unavailable
UCMR5_OCCURRENCE_TSV_URL = (
    "https://www.epa.gov/system/files/other-files/2023-08/"
    "ucmr5-occurrence-data.tsv"
)


class EPAUCMRSource(DataSource):
    """Connector for EPA UCMR 5 PFAS monitoring bulk data.

    Downloads the national occurrence data file once, caches in memory,
    then filters by state for each fetch_facilities/fetch_violations call.
    """

    name = "epa_ucmr"

    # Name of the main occurrence data file inside the zip.
    # The zip also contains UCMR5_AddtlDataElem.txt and UCMR5_ZIPCodes.txt;
    # we must select the main data file explicitly rather than using the first entry.
    OCCURRENCE_FILENAME = "UCMR5_All.txt"
    ZIPCODES_FILENAME = "UCMR5_ZIPCodes.txt"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=120.0, follow_redirects=True)
        self._all_rows: list[dict] | None = None
        # PWSID -> first ZIP code from UCMR5_ZIPCodes.txt (populated by _try_download_zip)
        self._pwsid_zips: dict[str, str] = {}

    def _download_and_parse(self) -> list[dict]:
        """Download the UCMR 5 occurrence data and parse into row dicts."""
        if self._all_rows is not None:
            return self._all_rows

        logger.info("Downloading UCMR 5 occurrence data...")

        # Try zip download first, fall back to direct TSV
        rows = self._try_download_zip()
        if rows is None:
            rows = self._try_download_tsv()

        if rows is None:
            logger.error("Failed to download UCMR 5 data from both zip and TSV URLs")
            self._all_rows = []
            return self._all_rows

        self._all_rows = rows
        logger.info("Parsed %s UCMR 5 sample rows", len(rows))
        return self._all_rows

    def _try_download_zip(self) -> list[dict] | None:
        """Try downloading and extracting from the zip file.

        Reads UCMR5_All.txt for occurrence data and UCMR5_ZIPCodes.txt for
        PWSID-to-ZIP mapping. The zip uses latin-1 encoding (not UTF-8).
        """
        try:
            resp = self._client.get(UCMR5_OCCURRENCE_URL)
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                names = zf.namelist()

                # Find the main occurrence data file by known name, fall back to
                # any .tsv/.txt file if the expected name is not present.
                if self.OCCURRENCE_FILENAME in names:
                    target = self.OCCURRENCE_FILENAME
                else:
                    tsv_names = [n for n in names if n.endswith(('.tsv', '.txt'))]
                    if not tsv_names:
                        logger.warning("No TSV/TXT file found in UCMR 5 zip")
                        return None
                    target = tsv_names[0]
                    logger.warning(
                        "%s not found in zip, falling back to %s",
                        self.OCCURRENCE_FILENAME, target,
                    )

                logger.info("Extracting %s from zip...", target)
                with zf.open(target) as f:
                    # UCMR data uses latin-1 encoding (contains µg/L symbol etc.)
                    text = f.read().decode("latin-1")
                    rows = self._parse_tsv(text)

                # Parse the ZIP code lookup file if present
                if self.ZIPCODES_FILENAME in names:
                    logger.info("Extracting %s for PWSID→ZIP mapping...", self.ZIPCODES_FILENAME)
                    with zf.open(self.ZIPCODES_FILENAME) as f:
                        zip_text = f.read().decode("latin-1")
                        self._pwsid_zips = self._parse_zip_lookup(zip_text)
                        logger.info("Loaded ZIP codes for %d PWSIDs", len(self._pwsid_zips))

                return rows

        except Exception as e:
            logger.warning("Zip download failed: %s, trying TSV fallback", e)
            return None

    def _try_download_tsv(self) -> list[dict] | None:
        """Try downloading the direct TSV file."""
        try:
            resp = self._client.get(UCMR5_OCCURRENCE_TSV_URL)
            resp.raise_for_status()
            text = resp.content.decode("latin-1")
            return self._parse_tsv(text)
        except Exception as e:
            logger.error("TSV download failed: %s", e)
            return None

    def _parse_tsv(self, text: str) -> list[dict]:
        """Parse tab-delimited UCMR data into row dicts."""
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        rows = []
        for row in reader:
            rows.append(row)
        return rows

    def _parse_zip_lookup(self, text: str) -> dict[str, str]:
        """Parse UCMR5_ZIPCodes.txt into a PWSID -> ZIP code dict.

        The file has columns PWSID and ZIPCODE. Some PWSIDs have multiple ZIPs
        (when a water system spans multiple ZIP codes); we store the first one.
        """
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        lookup: dict[str, str] = {}
        for row in reader:
            pwsid = (row.get("PWSID") or "").strip()
            zipcode = (row.get("ZIPCODE") or "").strip()
            if pwsid and zipcode and pwsid not in lookup:
                lookup[pwsid] = zipcode
        return lookup

    def _filter_state(self, rows: list[dict], state: str) -> list[dict]:
        """Filter rows to a specific state.

        UCMR data uses PrimacyAgency for state (2-letter code).
        Also checks State field as fallback. The PWSID starts with
        the 2-letter state code, so we can use that as a tertiary check.
        """
        state_upper = state.upper()
        filtered = []
        for row in rows:
            # Primary: PrimacyAgency field
            pa = (row.get("PrimacyAgency") or "").strip().upper()
            if pa == state_upper:
                filtered.append(row)
                continue

            # Secondary: State field
            st = (row.get("State") or "").strip().upper()
            if st == state_upper:
                filtered.append(row)
                continue

            # Tertiary: PWSID prefix (e.g., "TX0100001" starts with "TX")
            pwsid = (row.get("PWSId") or row.get("PWSID") or "").strip().upper()
            if pwsid[:2] == state_upper:
                filtered.append(row)
                continue

        return filtered

    def fetch_facilities(self, state: str) -> Iterator[Facility]:
        """Yield UCMR 5 water system facilities for a given state."""
        all_rows = self._download_and_parse()
        state_rows = self._filter_state(all_rows, state)

        if not state_rows:
            logger.info("No UCMR 5 data for %s", state)
            return

        facilities = aggregate_pws_facilities(state_rows, pwsid_zips=self._pwsid_zips)
        logger.info("%s: %s UCMR 5 facilities from %s samples", state, len(facilities), len(state_rows))
        yield from facilities

    def fetch_violations(self, state: str) -> Iterator[Violation]:
        """Yield violations for water systems exceeding PFAS MCLs."""
        all_rows = self._download_and_parse()
        state_rows = self._filter_state(all_rows, state)

        if not state_rows:
            logger.info("No UCMR 5 violation data for %s", state)
            return

        violations = aggregate_pws_violations(state_rows)
        logger.info("%s: %s PFAS MCL exceedance violations", state, len(violations))
        yield from violations

    def close(self) -> None:
        self._client.close()
