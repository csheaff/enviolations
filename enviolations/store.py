"""Batch upsert from model iterators into SQLite."""

from __future__ import annotations

import sqlite3
from typing import Iterator

from .config import BATCH_SIZE
from .db import upsert_facility, upsert_violation
from .models import Facility, Violation


def store_facilities(conn: sqlite3.Connection, facilities: Iterator[Facility]) -> int:
    """Batch-upsert facilities. Returns count of records stored."""
    count = 0
    batch = 0
    for facility in facilities:
        upsert_facility(conn, facility.model_dump(mode="json"))
        count += 1
        batch += 1
        if batch >= BATCH_SIZE:
            conn.commit()
            batch = 0
    if batch > 0:
        conn.commit()
    return count


def store_violations(conn: sqlite3.Connection, violations: Iterator[Violation]) -> int:
    """Batch-upsert violations. Returns count of records stored."""
    count = 0
    batch = 0
    for violation in violations:
        upsert_violation(conn, violation.model_dump(mode="json"))
        count += 1
        batch += 1
        if batch >= BATCH_SIZE:
            conn.commit()
            batch = 0
    if batch > 0:
        conn.commit()
    return count
