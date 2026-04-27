import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ENVIOLATIONS_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = Path(os.environ.get("ENVIOLATIONS_DB_PATH", str(DATA_DIR / "pipeline.db")))

EPA_ECHO_BASE_URL = os.environ.get("EPA_ECHO_BASE_URL", "https://echodata.epa.gov")
EPA_API_KEY = os.environ.get("EPA_API_KEY", "")

# EPA ECHO rate limit: 1,000 req/hour — we only need ~5-10 per ingest
RATE_LIMIT_DELAY = float(os.environ.get("ENVIOLATIONS_RATE_LIMIT_DELAY", "3.6"))

BATCH_SIZE = int(os.environ.get("ENVIOLATIONS_BATCH_SIZE", "500"))

# Used by the optional reference HTTP API (examples/api/) to disable
# /docs and /redoc when running in production. Set ENVIOLATIONS_ENV=production
# to harden the API surface.
ENVIOLATIONS_ENV = os.environ.get("ENVIOLATIONS_ENV", "development")
