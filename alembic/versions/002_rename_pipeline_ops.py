"""rename ingestion_log to pipeline_ops and add columns

Revision ID: 002_pipeline_ops
Revises: 001_baseline
Create Date: 2026-02-26

Renames ingestion_log -> pipeline_ops and adds columns for broader
pipeline operation tracking (not just ingests):
  - command_type: ingest, score, resolve, rebuild, geocode
  - triggered_by: manifest-CIV-180, cron, manual
  - manifest_id: source manifest filename (nullable)
  - details: JSON metadata (nullable)

Creates a backward-compatible VIEW named 'ingestion_log' pointing at
pipeline_ops so that unremodified API code continues to work.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_pipeline_ops"
down_revision: Union[str, None] = "001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    has_pipeline_ops = inspector.has_table("pipeline_ops")
    has_ingestion_log = inspector.has_table("ingestion_log")

    if has_pipeline_ops:
        # Table already exists (e.g. interrupted migration retry or previous
        # partial migration). Ensure the view exists and return.
        _create_ingestion_log_view()
        return

    if not has_ingestion_log:
        # Fresh DB where baseline didn't create ingestion_log yet.
        # Create pipeline_ops from scratch.
        _create_pipeline_ops()
        _create_ingestion_log_view()
        return

    # Normal path: rename existing table and add new columns.
    op.rename_table("ingestion_log", "pipeline_ops")

    with op.batch_alter_table("pipeline_ops") as batch_op:
        batch_op.add_column(sa.Column("command_type", sa.Text, server_default="ingest"))
        batch_op.add_column(sa.Column("triggered_by", sa.Text))
        batch_op.add_column(sa.Column("manifest_id", sa.Text))
        batch_op.add_column(sa.Column("details", sa.Text))

    # Create backward-compatible VIEW so API code referencing
    # ingestion_log continues to work without modification.
    _create_ingestion_log_view()


def _create_pipeline_ops() -> None:
    """Create pipeline_ops table from scratch (fresh DB path)."""
    op.create_table(
        "pipeline_ops",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("started_at", sa.Text, nullable=False),
        sa.Column("completed_at", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("records_processed", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text),
        sa.Column("command_type", sa.Text, server_default="ingest"),
        sa.Column("triggered_by", sa.Text),
        sa.Column("manifest_id", sa.Text),
        sa.Column("details", sa.Text),
    )


def _create_ingestion_log_view() -> None:
    """Create a backward-compatible VIEW aliasing pipeline_ops.

    API routes and other unremodified code can continue to SELECT from
    ingestion_log transparently. The VIEW exposes only the original columns
    so the schema contract is unchanged for readers.
    """
    op.execute(
        "CREATE VIEW IF NOT EXISTS ingestion_log AS "
        "SELECT id, source, started_at, completed_at, status, "
        "records_processed, error_message "
        "FROM pipeline_ops"
    )


def downgrade() -> None:
    # Drop the compatibility view first
    op.execute("DROP VIEW IF EXISTS ingestion_log")

    # Remove new columns
    with op.batch_alter_table("pipeline_ops") as batch_op:
        batch_op.drop_column("details")
        batch_op.drop_column("manifest_id")
        batch_op.drop_column("triggered_by")
        batch_op.drop_column("command_type")

    # Rename back
    op.rename_table("pipeline_ops", "ingestion_log")
