"""Backfill status for TCEQ NOE violations

Revision ID: 006_backfill_tceq_noe_statuses
Revises: 005_fix_cross_state_collisions
Create Date: 2026-03-12

TCEQ Notice of Enforcement (NOE) violations were ingested before CIV-507
added the date-based status heuristic to map_noe().  All 26,414 existing
NOE records in the violations table have a NULL status, which the dashboard
displays as "Status unknown" — confusing for end users who see it alongside
NOV violations that carry real status values (Active, Resolved, etc.).

Fix: Backfill the status column for all TCEQ NOE violations using the same
5-year date heuristic implemented in pipeline/normalize/tceq_mapper.py:

  violation_date < 5 years ago  → "Resolved"
  violation_date >= 5 years ago → "Active"
  violation_date IS NULL        → "Active"

This mirrors _derive_status() exactly.  After this migration, NOE violations
will display correct status badges in the facility drawer, not "Status unknown".
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "006_backfill_tceq_noe_statuses"
down_revision: Union[str, None] = "005_fix_cross_state_collisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Set status = 'Resolved' for old NOE violations (>5 years ago).
    op.execute(
        """
        UPDATE violations
        SET status = 'Resolved'
        WHERE source = 'tceq'
          AND violation_type = 'NOE'
          AND (status IS NULL OR status = '')
          AND violation_date IS NOT NULL
          AND violation_date < date('now', '-5 years')
        """
    )

    # Set status = 'Active' for recent NOE violations (≤5 years ago or no date).
    op.execute(
        """
        UPDATE violations
        SET status = 'Active'
        WHERE source = 'tceq'
          AND violation_type = 'NOE'
          AND (status IS NULL OR status = '')
        """
    )


def downgrade() -> None:
    # Restore NULL status for all TCEQ NOE violations.
    op.execute(
        """
        UPDATE violations
        SET status = NULL
        WHERE source = 'tceq'
          AND violation_type = 'NOE'
          AND status IN ('Active', 'Resolved')
        """
    )
