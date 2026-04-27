"""null island coordinate cleanup

Revision ID: 003_null_island_cleanup
Revises: 002_pipeline_ops
Create Date: 2026-03-03

Sets lat/lon to NULL for facilities where both coordinates are exactly (0, 0).
These are null island records — geocoding sentinels that ended up stored instead
of being rejected. The Facility model validator now prevents future ingestion
of null island coordinates, so this migration cleans up existing bad data.

Also clears unified_facilities rows with (0,0) coordinates, which will be
regenerated correctly on the next rebuild-unified run.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "003_null_island_cleanup"
down_revision: Union[str, None] = "002_pipeline_ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE facilities SET lat = NULL, lon = NULL WHERE lat = 0 AND lon = 0"
    )
    op.execute(
        "UPDATE unified_facilities SET lat = NULL, lon = NULL WHERE lat = 0 AND lon = 0"
    )


def downgrade() -> None:
    # Cannot restore original (0,0) values — they were invalid sentinel values.
    pass
