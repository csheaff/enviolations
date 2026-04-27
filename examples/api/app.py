"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from enviolations.config import ENVIOLATIONS_ENV
from enviolations.db import init_db
from enviolations.geo import warm_zip_centroid_cache
from .cache import get_cache
from .middleware import NoCacheHTMLMiddleware, SecurityHeadersMiddleware, UsageTrackingMiddleware
from .routes import _compute_coverage, _compute_stats, public_router, router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger("pipeline.api")
    log.info("Starting API server")
    conn = init_db()
    warm_zip_centroid_cache(conn)
    # Pre-warm the stats cache so /health returns counts immediately on startup
    # rather than null (which happens when /stats has not yet been called).
    # _compute_stats() opens its own connection, so we pass the init conn only
    # for the DB-ready check above; stats warming is handled separately.
    try:
        stats = _compute_stats()
        get_cache().set("stats", stats)
        log.info(
            "Stats cache pre-warmed: %d facilities, %d violations",
            stats["total_facilities"],
            stats["total_violations"],
        )
    except Exception as exc:
        log.warning("Failed to pre-warm stats cache: %s", exc)
    try:
        coverage = _compute_coverage()
        get_cache().set("coverage:", coverage)
        log.info(
            "Coverage cache pre-warmed: %d states, %d facilities",
            coverage["summary"]["total_states"],
            coverage["summary"]["total_facilities"],
        )
    except Exception as exc:
        log.warning("Failed to pre-warm coverage cache: %s", exc)
    conn.close()
    yield


def create_app() -> FastAPI:
    is_production = ENVIOLATIONS_ENV == "production"
    app = FastAPI(
        title="Environmental Compliance Data API",
        version="0.1.0",
        description=(
            "Aggregated environmental compliance data from federal (EPA) and state "
            "sources. Search facilities by location, filter violations by date, "
            "generate environmental screening reports, and export to CSV.\n\n"
            "**Disclaimer:** Data is aggregated from public government sources and "
            "provided as-is for informational purposes only. It is not a professional "
            "environmental site assessment. Data may be incomplete, delayed, or "
            "contain errors. Coverage varies by state. Always verify with primary sources.\n\n"
            "**No Warranty:** This service is provided without warranty of any kind. "
            "In no event shall the provider be liable for any damages arising from "
            "use of this data. Usage may be logged for quality and reliability improvements."
        ),
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
    )
    app.add_middleware(UsageTrackingMiddleware)
    app.add_middleware(NoCacheHTMLMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(public_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    # Serve the reference dashboard from the sibling examples/dashboard/ directory.
    static_dir = Path(__file__).parent.parent / "dashboard"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app
