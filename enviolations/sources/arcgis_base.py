"""ArcGIS FeatureServer/MapServer base class for source connectors.

Extracts common boilerplate shared by ~48 ArcGIS-based connectors:
  - HTTP client initialization and cleanup
  - Rate limiting
  - Paginated feature queries with retry logic
"""

from __future__ import annotations

import logging
import time

import httpx

from .base import DataSource

logger = logging.getLogger(__name__)

class ArcGISSource(DataSource):
    """Base class for ArcGIS REST API source connectors.

    Provides HTTP client management, rate limiting, and paginated
    feature queries with automatic retry. Subclasses define URLs
    and mappers in fetch_facilities/fetch_violations.

    Class attributes (override in subclasses):
        page_size: Records per request (default 2000)
        rate_limit_delay: Seconds between requests (default 1.0)
        timeout: HTTP timeout in seconds (default 300.0)
    """

    page_size: int = 2000
    rate_limit_delay: float = 1.0
    timeout: float = 300.0

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=self.timeout)
        self._last_request = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request = time.monotonic()

    def _query_features(
        self,
        url: str,
        label: str,
        *,
        where: str = "1=1",
        return_geometry: bool = True,
        page_size: int | None = None,
    ) -> list[dict]:
        """Query an ArcGIS FeatureServer/MapServer with pagination and retry.

        Args:
            url: ArcGIS REST query endpoint URL.
            label: Human-readable label for logging.
            where: SQL WHERE clause (default "1=1" for all records).
            return_geometry: Whether to include geometry (default True).
            page_size: Override self.page_size for this query.
        """
        ps = page_size if page_size is not None else self.page_size
        all_features: list[dict] = []
        offset = 0

        while True:
            self._rate_limit()
            params: dict[str, str] = {
                "where": where,
                "outFields": "*",
                "returnGeometry": "true" if return_geometry else "false",
                "f": "json",
                "resultOffset": str(offset),
                "resultRecordCount": str(ps),
            }
            if return_geometry:
                params["outSR"] = "4326"

            for attempt in range(3):
                try:
                    logger.info("Querying %s (offset=%s)...", label, offset)
                    resp = self._client.get(url, params=params, follow_redirects=True)

                    if resp.status_code >= 500 and attempt < 2:
                        wait = 15 * (attempt + 1)
                        logger.error(
                            "Server error %s, retrying in %ss...",
                            resp.status_code,
                            wait,
                        )
                        time.sleep(wait)
                        continue

                    # 403 from ArcGIS often means transient IP rate-limiting, not
                    # permanent permission denial. Retry with longer backoff before
                    # giving up.
                    if resp.status_code == 403 and attempt < 2:
                        wait = 30 * (attempt + 1)
                        logger.warning(
                            "HTTP 403 from %s (attempt %d), retrying in %ss...",
                            label,
                            attempt + 1,
                            wait,
                        )
                        time.sleep(wait)
                        continue

                    resp.raise_for_status()
                    break
                except httpx.TimeoutException:
                    if attempt < 2:
                        wait = 15 * (attempt + 1)
                        logger.info(
                            "Timeout querying %s, retrying in %ss...", label, wait
                        )
                        time.sleep(wait)
                    else:
                        raise
            else:
                break

            data = resp.json()

            if "error" in data:
                err = data["error"]
                logger.error("ArcGIS error: %s", err.get("message", err))
                break

            features = data.get("features", [])
            all_features.extend(features)

            page_count = len(features)
            logger.info(
                "Got %s features (total so far: %s)", page_count, len(all_features)
            )

            exceeded = data.get("exceededTransferLimit", False)
            if not exceeded or page_count < ps:
                break

            offset += ps

        logger.info("Total %s features: %s", label, len(all_features))
        return all_features

    def _query_socrata(
        self,
        url: str,
        label: str,
        *,
        page_size: int = 5000,
        order: str | None = None,
    ) -> list[dict]:
        """Fetch all records from a Socrata SODA JSON endpoint with pagination.

        Used by hybrid connectors that query both ArcGIS and Socrata.

        Args:
            url: Full Socrata SODA JSON endpoint URL.
            label: Human-readable label for logging.
            page_size: Records per request (default 5000).
            order: Optional $order param (e.g. ":id" for stable ordering).
        """
        all_records: list[dict] = []
        offset = 0

        while True:
            self._rate_limit()
            params: dict[str, str] = {
                "$limit": str(page_size),
                "$offset": str(offset),
            }
            if order:
                params["$order"] = order

            for attempt in range(3):
                try:
                    logger.info("Fetching %s (offset=%s)...", label, offset)
                    resp = self._client.get(url, params=params, follow_redirects=True)

                    if resp.status_code >= 500 and attempt < 2:
                        wait = 15 * (attempt + 1)
                        logger.error(
                            "Server error %s, retrying in %ss...",
                            resp.status_code,
                            wait,
                        )
                        time.sleep(wait)
                        continue

                    resp.raise_for_status()
                    break
                except httpx.TimeoutException:
                    if attempt < 2:
                        wait = 15 * (attempt + 1)
                        logger.info(
                            "Timeout fetching %s, retrying in %ss...", label, wait
                        )
                        time.sleep(wait)
                    else:
                        raise
            else:
                break

            records = resp.json()
            if not isinstance(records, list):
                logger.info("Unexpected response type: %s", type(records))
                break

            all_records.extend(records)
            page_count = len(records)
            logger.info(
                "Got %s records (total so far: %s)", page_count, len(all_records)
            )

            if page_count < page_size:
                break

            offset += page_size

        logger.info("Total %s records: %s", label, len(all_records))
        return all_records

    def close(self) -> None:
        self._client.close()
