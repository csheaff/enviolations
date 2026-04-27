"""normalize county names in facilities table

Revision ID: 004_normalize_county_names
Revises: 003_null_island_cleanup
Create Date: 2026-03-03

Strips county-type suffixes (COUNTY, PARISH, BOROUGH, etc.) from the county
column in facilities and unified_facilities and uppercases the result.

Examples:
  "HARRIS COUNTY" -> "HARRIS"
  "San Diego County" -> "SAN DIEGO"
  "SAN DIEGO COUNTY" -> "SAN DIEGO"
  "Orleans Parish" -> "ORLEANS"

Root cause (CIV-360): Different sources store county names with and without
suffixes. EPA ECHO uses bare names ("HARRIS"), while some state sources use
the full legal name ("HARRIS COUNTY"). This creates duplicate county entries
in facility counts and county-level analysis.

The Facility Pydantic model validator already strips these suffixes for new
ingests (added for CIV-278). This migration backfills existing DB rows that
were ingested before the validator was in place, or via sources that bypassed
the model.

The rebuild_unified_table function also normalizes county in unified_facilities
post-process, but that only runs during rebuild — existing rows need cleanup.
"""
import re
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "004_normalize_county_names"
down_revision: Union[str, None] = "003_null_island_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COUNTY_SUFFIX_RE = re.compile(
    r"\s+(?:county|parish|borough|census\s+area|municipality|city\s+and\s+borough)$",
    re.IGNORECASE,
)


def _normalize(val: str) -> str:
    """Strip county suffix and uppercase."""
    v = _COUNTY_SUFFIX_RE.sub("", val.strip()).strip()
    return v.upper() if v else val.upper()


def upgrade() -> None:
    conn = op.get_bind()

    # Normalize facilities table
    rows = conn.execute(
        text("SELECT source, source_id, county FROM facilities WHERE county IS NOT NULL")
    ).fetchall()
    updates = []
    for row in rows:
        normalized = _normalize(row[2])
        if normalized != row[2]:
            updates.append({"county": normalized, "source": row[0], "source_id": row[1]})
    for update in updates:
        conn.execute(
            text("UPDATE facilities SET county = :county WHERE source = :source AND source_id = :source_id"),
            update,
        )

    # Normalize unified_facilities table (if it exists and has data)
    has_unified = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='unified_facilities'")
    ).fetchone()
    if has_unified:
        uf_rows = conn.execute(
            text("SELECT source_id, county FROM unified_facilities WHERE county IS NOT NULL")
        ).fetchall()
        uf_updates = []
        for row in uf_rows:
            normalized = _normalize(row[1])
            if normalized != row[1]:
                uf_updates.append({"county": normalized, "source_id": row[0]})
        for update in uf_updates:
            conn.execute(
                text("UPDATE unified_facilities SET county = :county WHERE source_id = :source_id"),
                update,
            )


def downgrade() -> None:
    # Cannot restore original mixed-case/suffix values — they were inconsistent.
    pass
