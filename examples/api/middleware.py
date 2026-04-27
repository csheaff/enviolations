"""Usage tracking, rate limiting, and security-header middleware for the pipeline API."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import sqlite3

from enviolations import db as _db
from .auth import hash_api_key

logger = logging.getLogger("pipeline.api")

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

# Content-Security-Policy for the dashboard HTML pages.
# Pages use:
#   - Leaflet (CSS + JS) from unpkg.com
#   - Google Fonts (CSS from fonts.googleapis.com, assets from fonts.gstatic.com)
#   - Carto map tiles (*.basemaps.cartocdn.com)
#   - Extensive inline <style> and <script> blocks
# 'unsafe-inline' is required for scripts because the pages use inline JS.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https://*.basemaps.cartocdn.com https://*.openstreetmap.org https://fastapi.tiangolo.com; "
    "connect-src 'self'; "
    "frame-ancestors 'none';"
)

# One year in seconds -- appropriate for HSTS once HTTPS is confirmed.
_HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to every response.

    Headers added:
      - Content-Security-Policy
      - Strict-Transport-Security
      - X-Content-Type-Options: nosniff
      - X-Frame-Options: DENY
      - Referrer-Policy: strict-origin-when-cross-origin
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["Strict-Transport-Security"] = _HSTS
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


def _fast_connection() -> sqlite3.Connection:
    """Connection with a very short busy_timeout for non-critical middleware writes.

    Fails fast (500ms) instead of blocking requests for 30s when the DB is locked
    by long-running pipeline operations (refresh, ingest, resolve).
    """
    conn = sqlite3.connect(str(_db.DB_PATH), timeout=0.5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=500")
    return conn

DEFAULT_RATE_LIMIT = 100  # requests per hour per API key
ANON_RATE_LIMIT = 200  # requests per hour for unauthenticated users
DEFAULT_MONTHLY_LIMIT = 5000  # requests per 30-day rolling window per API key


class NoCacheHTMLMiddleware(BaseHTTPMiddleware):
    """Prevent browsers and proxies from caching dynamic content.

    HTML pages get no-cache so code updates are seen immediately.
    API responses (text/csv, application/json, application/pdf) get no-store
    to prevent stale search results from being served -- especially CSV exports
    which must always reflect the current search, not a prior one (CIV-475).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if "text/html" in ct:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        elif request.url.path.startswith("/api/"):
            # API responses must never be served from cache -- search results
            # and export files are query-specific and change with every request.
            if not response.headers.get("Cache-Control"):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
        return response


# Endpoints where we log search params (state + radius only, never full address)
_SEARCH_ENDPOINTS = frozenset({
    "/api/v1/search",
    "/api/v1/search/violations",
    "/api/v1/reports/screening",
})


def _extract_search_params(request: Request) -> str | None:
    """Extract loggable search params from a request. Returns JSON string or None.

    Only runs on search endpoints. Strips addresses to state-only for privacy.
    """
    if request.url.path not in _SEARCH_ENDPOINTS:
        return None

    from enviolations.geo import _extract_state

    params: dict = {}

    # Extract state from address (never store the full address)
    address = request.query_params.get("address")
    if address:
        state = _extract_state(address)
        if state:
            params["state"] = state

    # Also check explicit state param
    state_param = request.query_params.get("state")
    if state_param:
        params["state"] = state_param.upper()[:2]

    # Radius
    radius = request.query_params.get("radius")
    if radius:
        try:
            params["radius"] = float(radius)
        except ValueError:
            pass

    return json.dumps(params) if params else None


_KNOWN_SOURCES = frozenset({"eval", "smoke"})


class UsageTrackingMiddleware(BaseHTTPMiddleware):
    """Log every API request and enforce per-key rate limits."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only track /api/ requests (skip static files)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        raw_api_key = request.headers.get("X-API-Key")

        # Rate-limit check
        if raw_api_key:
            key_hash = hash_api_key(raw_api_key)
            try:
                conn = _fast_connection()
                try:
                    now_utc = datetime.now(timezone.utc)
                    one_hour_ago = (now_utc - timedelta(hours=1)).isoformat()
                    thirty_days_ago = (now_utc - timedelta(days=30)).isoformat()
                    count = conn.execute(
                        "SELECT COUNT(*) FROM api_usage WHERE api_key = ? AND timestamp > ?",
                        (key_hash, one_hour_ago),
                    ).fetchone()[0]
                    key_row = conn.execute(
                        "SELECT rate_limit, monthly_limit FROM api_keys WHERE key_hash = ?", (key_hash,)
                    ).fetchone()
                    limit = key_row[0] if key_row and key_row[0] else DEFAULT_RATE_LIMIT
                    monthly_limit = key_row[1] if key_row and key_row[1] else DEFAULT_MONTHLY_LIMIT
                    monthly_count = conn.execute(
                        "SELECT COUNT(*) FROM api_usage WHERE api_key = ? AND timestamp > ?",
                        (key_hash, thirty_days_ago),
                    ).fetchone()[0]
                finally:
                    conn.close()
                if count >= limit:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Rate limit exceeded", "limit": limit, "retry_after_seconds": 3600},
                        headers={"Retry-After": "3600"},
                    )
                if monthly_count >= monthly_limit:
                    # Find when the oldest call in the 30-day window will age out.
                    # When it does, the monthly count drops below the limit.
                    conn2 = _fast_connection()
                    try:
                        oldest_ts_row = conn2.execute(
                            "SELECT MIN(timestamp) FROM api_usage WHERE api_key = ? AND timestamp > ?",
                            (key_hash, thirty_days_ago),
                        ).fetchone()
                    finally:
                        conn2.close()
                    if oldest_ts_row and oldest_ts_row[0]:
                        oldest_ts = datetime.fromisoformat(oldest_ts_row[0])
                        reset_at = oldest_ts + timedelta(days=30)
                        days_until_reset = max(1, (reset_at - now_utc).days + 1)
                    else:
                        days_until_reset = 1
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": f"Monthly API call limit exceeded. Resets in {days_until_reset} day(s).",
                            "limit": monthly_limit,
                            "retry_after_seconds": days_until_reset * 86400,
                        },
                        headers={"Retry-After": str(days_until_reset * 86400)},
                    )
            except Exception:
                logger.warning("Rate limit check failed", exc_info=True)
            api_key = key_hash
        else:
            # Anonymous rate limiting by IP
            ip = (
                request.headers.get("CF-Connecting-IP")
                or (request.client.host if request.client else "unknown")
            )
            api_key = f"anon:{ip}"
            try:
                conn = _fast_connection()
                try:
                    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                    count = conn.execute(
                        "SELECT COUNT(*) FROM api_usage WHERE api_key = ? AND timestamp > ?",
                        (api_key, one_hour_ago),
                    ).fetchone()[0]
                finally:
                    conn.close()
                if count >= ANON_RATE_LIMIT:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": "Anonymous rate limit exceeded. Request an API key for higher limits.",
                            "limit": ANON_RATE_LIMIT,
                            "retry_after_seconds": 3600,
                        },
                        headers={"Retry-After": "3600"},
                    )
            except Exception:
                logger.warning("Anonymous rate limit check failed", exc_info=True)

        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        timestamp = datetime.now(timezone.utc).isoformat()

        # Classify traffic source (eval agents, smoke tests, etc.)
        raw_source = request.headers.get("X-Source")
        source = raw_source if raw_source in _KNOWN_SOURCES else None

        try:
            conn = _fast_connection()
            try:
                conn.execute(
                    "INSERT INTO api_usage (timestamp, method, endpoint, api_key, response_time_ms, status_code, params, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (timestamp, request.method, request.url.path, api_key, round(elapsed_ms, 2), response.status_code, _extract_search_params(request), source),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.warning("Usage logging failed", exc_info=True)

        return response
