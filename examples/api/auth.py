"""API key authentication for the pipeline API."""

from __future__ import annotations

import hashlib

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from enviolations.db import get_connection

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def hash_api_key(key: str) -> str:
    """Return the sha256 hex digest of an API key."""
    return hashlib.sha256(key.encode()).hexdigest()


def require_api_key(api_key: str | None = Security(_API_KEY_HEADER)) -> str | None:
    """Validate the API key from the X-API-Key header.

    If no API keys exist in the database (dev mode), all requests are allowed.
    Once at least one key is created, authentication is enforced.
    """
    conn = get_connection()
    try:
        has_keys = conn.execute("SELECT 1 FROM api_keys LIMIT 1").fetchone()
        if not has_keys:
            return None  # No keys ever created — dev mode, allow all

        if api_key is None:
            raise HTTPException(status_code=401, detail="Missing API key")

        key_hash = hash_api_key(api_key)
        valid = conn.execute(
            "SELECT key_hash FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ).fetchone()
        if not valid:
            raise HTTPException(status_code=401, detail="Invalid API key")

        return api_key
    finally:
        conn.close()
