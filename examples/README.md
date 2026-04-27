# Examples — Reference API and Dashboard

This directory contains a working reference implementation of an HTTP API and web dashboard built on top of the `enviolations` library. Treat it as a starting point, not a finished product — fork and adapt freely.

## What's here

```
examples/
├── api/              FastAPI HTTP server (16 endpoints)
│   ├── app.py        FastAPI factory + lifespan
│   ├── routes.py     /api/v1/* endpoints (search, screening, exports)
│   ├── auth.py       Optional API-key authentication (header: X-API-Key)
│   ├── middleware.py Rate limiting, security headers, usage logging
│   ├── cache.py      In-process LRU cache for hot endpoints
│   ├── pdf.py        Phase I-style screening report generator (reportlab)
│   ├── services.py   Shared business logic (search, dedup, screening)
│   └── cfr_citations.py   Federal regulation lookup for violation enrichment
├── dashboard/        Static HTML/JS single-page application
│   ├── index.html    Search, facility detail, map view, screening reports
│   └── methodology.html   Scoring methodology reference
└── requirements.txt  Additional deps (fastapi, uvicorn, reportlab, matplotlib)
```

## Running it

```bash
# Install the library + example deps
pip install -r requirements.txt -r examples/requirements.txt

# Build / refresh the local DB (see main README "Quick start")
# ...

# Start the server
uvicorn examples.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

Then open:
- Dashboard: http://localhost:8000/
- API docs (Swagger): http://localhost:8000/docs
- API: http://localhost:8000/api/v1/health

In production, set `ENVIOLATIONS_ENV=production` to disable `/docs` and `/redoc`.

## Endpoints

The API exposes ~16 routes under `/api/v1/`:

- `GET /facilities` — list with filters (state, source, score range)
- `GET /facilities/{source}/{source_id}` — facility detail
- `GET /facilities/{source}/{source_id}/violations` — violation history
- `GET /unified-facilities` — deduplicated cross-source view
- `GET /search` — radius search by lat/lon or address
- `GET /screening-report` — Phase I-style report (PDF or JSON)
- `GET /violations.csv` — bulk export
- `GET /stats`, `/sources`, `/coverage`, `/methodology`, `/health`, `/terms`

Full schema browsable at `/docs` (Swagger UI) when running.

## Architecture notes

- **API key auth is optional.** `auth.py` supports an `X-API-Key` header backed by the `api_keys` table. If you don't provision keys, all routes are public — fine for local dev or read-only public deployments. To enable, insert rows into `api_keys` and uncomment the `Depends(require_api_key)` parameters in `routes.py`.
- **Usage tracking.** `middleware.py` logs every request to the `api_usage` table (timestamp, endpoint, response time, status). Useful for analytics; drop the middleware in `app.py` if you don't want it.
- **Rate limiting** is per-key (or per-IP if no key). Adjust thresholds in `middleware.py`.
- **The dashboard is a single HTML file** with embedded CSS and vanilla JS. No build step. Calls `/api/v1/*` endpoints. ~3K lines.

## Scope

This is example/reference code, not a maintained product. It comes from earlier work and is presented as-is. Fork it, strip what you don't need, and build what you do. The library itself (`enviolations/`) is the supported surface; this directory is here so you don't have to invent the API + UI from scratch if you want them.
