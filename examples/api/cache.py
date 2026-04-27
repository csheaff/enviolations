"""In-memory TTL cache with stale-while-revalidate for expensive aggregate endpoints.

Used by /stats and /coverage to avoid running full-table COUNT queries on
every request against the 5M+ facility database.

Cache behaviour:
- Fresh (within TTL_SECONDS=3600): served directly from memory, sub-millisecond.
- Stale (within STALE_SECONDS=86400): served immediately from the previous value
  while a background thread refreshes the cache asynchronously.  The caller
  provides a ``refresh_fn`` callable; if none is provided the stale value is
  returned as-is until the next explicit ``set()`` call.
- Expired beyond STALE_SECONDS: treated as a cache miss (None).

Thread safety: The GIL provides sufficient protection for dict reads/writes
in CPython. The ``_refreshing`` set prevents duplicate background refreshes
for the same key.  For production use with multiple workers, this cache is
per-process — each worker caches independently, which is fine since the
data is the same and we only need eventual consistency.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

TTL_SECONDS = 3600       # 1 hour — serve fresh within this window
STALE_SECONDS = 86400    # 24 hours — serve stale + background-refresh within this window


class TTLCache:
    """In-memory key-value cache with per-entry TTL and stale-while-revalidate."""

    def __init__(self, ttl: int = TTL_SECONDS, stale: int = STALE_SECONDS) -> None:
        self._ttl = ttl
        self._stale = stale
        # Maps key → (value, fresh_until, stale_until)
        self._store: dict[str, tuple[Any, float, float]] = {}
        # Keys currently being refreshed in a background thread
        self._refreshing: set[str] = set()

    def get(self, key: str, refresh_fn: Callable[[], Any] | None = None) -> Any | None:
        """Return cached value for key, or None if missing/fully expired.

        If the entry is stale (past TTL but within STALE_SECONDS) and a
        ``refresh_fn`` is provided, the stale value is returned immediately
        and ``refresh_fn`` is scheduled in a background thread so the *next*
        request gets a fresh value.
        """
        entry = self._store.get(key)
        if entry is None:
            return None
        value, fresh_until, stale_until = entry
        now = time.monotonic()
        if now <= fresh_until:
            # Still fresh — serve directly.
            return value
        if now <= stale_until:
            # Stale but within grace window — serve immediately, trigger background refresh.
            if refresh_fn is not None and key not in self._refreshing:
                self._refreshing.add(key)
                t = threading.Thread(
                    target=self._background_refresh,
                    args=(key, refresh_fn),
                    daemon=True,
                    name=f"cache-refresh-{key}",
                )
                t.start()
            return value
        # Fully expired — treat as a miss.
        self._store.pop(key, None)
        return None

    def _background_refresh(self, key: str, refresh_fn: Callable[[], Any]) -> None:
        """Run refresh_fn and update cache; called from a daemon thread."""
        try:
            value = refresh_fn()
            if value is not None:
                self.set(key, value)
        except Exception:
            logger.exception("Background cache refresh failed for key %r", key)
        finally:
            self._refreshing.discard(key)

    def set(self, key: str, value: Any) -> None:
        """Store value under key with the configured TTL."""
        now = time.monotonic()
        self._store[key] = (value, now + self._ttl, now + self._stale)

    def invalidate(self, key: str) -> None:
        """Remove a cached entry if present."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all cached entries."""
        self._store.clear()


# Module-level cache instance shared across all requests in the process
_cache = TTLCache()


def get_cache() -> TTLCache:
    """Return the shared module-level cache instance."""
    return _cache
