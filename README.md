# enviolations

A Python framework for aggregating fragmented government environmental compliance data into a normalized SQLite database, with cross-source entity resolution and risk scoring.

> **Not for regulated environmental due diligence (Phase I ESAs), compliance attestations, lending, insurance, or any decision with material legal or financial consequences.** See [Disclaimer](#disclaimer) at the bottom.

A pre-computed snapshot of the data this code produces is also published as a Hugging Face dataset:
**https://huggingface.co/datasets/claysheaff/enviolations** — 7.3M facilities, 1.1M violations, ~715 MB of Parquet files. CC0 licensed. Same caveats apply.

## What it does

Government environmental data is fragmented across EPA programs (ECHO, SDWA, RCRA, CAA, SEMS) and 50+ state agencies, each with their own API conventions, schemas, and quirks. This library:

1. **Pulls** facility and violation records from multiple sources via a uniform `DataSource` interface
2. **Normalizes** them into Pydantic models (`Facility`, `Violation`) with typed fields and validation
3. **Stores** them in SQLite with idempotent upserts on `(source, source_id)` compound keys
4. **Resolves** cross-source duplicates via a 4-tier matching pipeline (address → geo → name → fuzzy)
5. **Scores** facility risk on a 0–100 scale (0 = clean, 100 = worst), with NAICS adjustments and program-type deductions

## What's included

74 connectors covering EPA programs and state environmental agencies:

- **EPA**: ECHO, RCRA (hazardous waste), CAA (air), SDWA (drinking water), SEMS (Superfund), UCMR (unregulated contaminants), PFAS
- **State agencies**: 50 states + DC. All 10 EPA regions represented. ArcGIS, Socrata, REST, GIS, and CSV-export patterns.
- **PFAS-specific**: dedicated EPA, Illinois, Michigan, New Jersey, Ohio, and Wisconsin PFAS sources

A few useful reference points when building a new connector:

| Source | Pattern | Notes |
|--------|---------|-------|
| `epa_echo` | EPA two-step QID API | National coverage. Search returns a query ID; second request paginates JSON 5000/page. |
| `epa_sdwa` | EPA Safe Drinking Water Act API | Different shape from ECHO — uses `WaterSystems` key, `PWSId` (not `RegistryID`), no lat/lon. |
| `tceq` | Texas Socrata + CSV exports | State-level pattern. Uses Census batch geocoding for unmapped addresses. |
| `arcgis_base` | Shared base for ArcGIS REST | Used by ~20 state sources. Handles `where=1=1` queries, paging, geometry parsing. |

Full source list: see `enviolations/sources/`.

## What's NOT included

Deliberately out of scope for this library:

- **API server** (FastAPI) — well-trodden pattern; wrap the library in a REST API yourself in an afternoon if you need one.
- **MCP server** — same reasoning.
- **Dashboard / UI** — out of scope.
- **CLI dispatcher** — write your own driver; the quick-start below shows the pattern.

## Requirements

- Python 3.11+ (the code uses PEP 604 union syntax — `str | None` — at runtime, which 3.9 doesn't accept)
- See `requirements.txt` for library dependencies

## Quick start

```bash
pip install -r requirements.txt
export EPA_API_KEY=your_key_here  # https://echodata.epa.gov/echo/echo_user_key_request.html

python -c "
from enviolations.db import init_db
from enviolations.sources.epa_echo import EPAEchoSource
from enviolations.store import store_facilities, store_violations

conn = init_db()
src = EPAEchoSource()
store_facilities(conn, src.fetch_facilities('TX'))
store_violations(conn, src.fetch_violations('TX'))
"
```

This creates `data/pipeline.db` with TX facilities and violations. Add more states or sources by importing additional connectors.

## Architecture

```
sources/        Concrete DataSource implementations (one per upstream)
normalize/      Per-source mappers: raw API → Pydantic models
models.py       Facility, Violation pydantic models
store.py        Batch upsert into SQLite
db.py           Schema, connection management, helper queries
resolve.py      Cross-source entity resolution (4 tiers)
scoring.py      0–100 risk scoring with NAICS and program adjustments
geo.py          Census Bureau + Nominatim geocoding, haversine
alembic/        Schema migrations
```

## Adding a new source

Three files per source:

1. `sources/<name>.py` — implement `DataSource` ABC (`fetch_facilities`, `fetch_violations` iterators)
2. `normalize/<name>_mapper.py` — parse raw records into `Facility` / `Violation` Pydantic models
3. Register in `resolve.py:STATE_SOURCE_MAP` if it's a state-level source (prevents cross-state false matches)

Look at `sources/tceq.py` + `normalize/tceq_mapper.py` for a state-source reference. Look at `sources/epa_echo.py` + `normalize/echo_mapper.py` for an EPA-style reference.

## Known issues and gotchas

### Ecosystem-wide upstream limitations

These are inherent to the government source data and apply across all sources:

- **Name truncation at 30 characters.** EPA ECHO and many state APIs cap facility names. There is nothing to join against — the full name doesn't exist in the upstream data.
- **Violation date gaps.** Some sources (notably Indiana spill records) have ~95% null dates. The dates don't exist in the upstream API.
- **Sentinel/centroid coordinates.** ~68K EPA facilities use county or state centroid coordinates instead of real GPS. Mitigated by sentinel detection in `resolve.py`, but the underlying coordinates can't be improved without a separate geocoding source mapping EPA RegistryIDs to real locations.

Plan around these — don't try to fix them in the mapper.

### Source-specific gotchas

Caveats accumulated during development. None are blockers; most are documented but unfixed.

- **`epa_echo` GeoJSON endpoint is flaky.** `https://echodata.epa.gov/echo/echo_rest_services.get_geojson` periodically returns HTTP 502 Proxy Error, especially on large states (CA). The connector retries; persistent failures usually clear within an hour.
- **`tn_tdec` may return HTTP 403** on all 5 ArcGIS endpoints (APC Permits, UST, DOR Sites, SWM Permits, GWP Complaints) when called from server-class IPs. Last successful pull was 2026-02-21; appears to be IP-based blocking or a User-Agent check. Try a residential IP or a browser-like User-Agent if you hit this.
- **`pa_dep_gis` Storage Tanks: numeric years in the `city` field.** For `tank-51-*` records, the upstream `MUNICIPALITY` field sometimes contains the installation year (e.g. `"1975"`) instead of a city name. The mapper does not currently filter these. If you query by city, expect garbage values for storage-tank facilities. ~6 confirmed cases in PA; unaudited at scale.
- **`nj_pfas` records are missing ZIP codes.** The PFAS survey layer doesn't include zip in its attributes. The data exists on a sibling NJ Air Quality layer and could be backfilled by joining on facility ID, but the connector doesn't currently do that.
- **`mi_egle` only covers air sources from 2018+.** Michigan state coverage is thinner than other industrial states because hazmat and remediation programs aren't covered by the state-level connector. For comprehensive MI coverage, rely on federal EPA sources (ECHO, RCRA) plus `mi_egle`.
- **`sc_des` URLs reflect the 2024 SCDHEC reorganization.** The Permits URL is confirmed (gis.des.sc.gov); the PWS URL is a best-guess based on the rename pattern and may 404. The `BEHS_Complaints` dataset (formerly the SC violations source) was retired with the ePermitting system — for SC violations, use federal EPA ECHO. See `enviolations/sources/sc_des.py` for details.

## Score convention

`0` = clean (low risk), `100` = worst (high risk). This matches industry standards (EPA HRS, HUD NSPIRE). Do not invert it.

## Status

Maintenance is best-effort. Issues and PRs may sit a while; if you need something fixed urgently, fork freely.

## Disclaimer

This software and any data it produces are provided **AS IS**, without warranty of any kind, express or implied. The library aggregates data from public US federal and state environmental agency sources; transcription, geocoding, normalization, and entity-resolution errors are possible and have been observed (see "Known issues" above).

**Do NOT use this for:**
- ASTM E1527-21 Phase I Environmental Site Assessments or any other regulated environmental due-diligence work product
- Regulatory compliance attestations or filings
- Lending decisions, insurance underwriting, or actuarial analysis
- Legal proceedings, real estate transactions, or any decision with material legal or financial consequences

For authoritative facility records, **contact the source agency directly** (EPA via [echo.epa.gov](https://echo.epa.gov/), or the relevant state environmental agency).

**Takedown / correction requests:** open an issue on this repository. Maintenance is best-effort; issues will be reviewed when time allows.

The author makes no representation that any specific facility, violation, or score reflects current compliance status, and disclaims all liability for decisions made in reliance on this software or its output.

## License

MIT
