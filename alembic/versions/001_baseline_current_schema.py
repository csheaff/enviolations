"""baseline_current_schema

Revision ID: 001_baseline
Revises:
Create Date: 2026-02-26

Full schema creation for the VDB pipeline database. On fresh databases this
creates all tables and indexes. On existing databases already stamped at this
revision, Alembic skips it entirely.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # If tables already exist (pre-Alembic database), just stamp the version.
    conn = op.get_bind()
    if sa.inspect(conn).has_table("facilities"):
        return

    op.create_table(
        "facilities",
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("address", sa.Text),
        sa.Column("city", sa.Text),
        sa.Column("state", sa.Text),
        sa.Column("zip_code", sa.Text),
        sa.Column("county", sa.Text),
        sa.Column("lat", sa.Float),
        sa.Column("lon", sa.Float),
        sa.Column("naics_codes", sa.Text),
        sa.Column("sic_codes", sa.Text),
        sa.Column("programs", sa.Text),
        sa.Column("last_updated", sa.Text, nullable=False),
        sa.UniqueConstraint("source", "source_id"),
    )
    op.create_index("idx_facilities_state", "facilities", ["state"])
    op.create_index("idx_facilities_county", "facilities", ["county"])
    op.create_index("idx_facilities_zip", "facilities", ["zip_code"])
    op.create_index("idx_facilities_source_id", "facilities", ["source_id"])
    op.create_index("idx_facilities_source", "facilities", ["source"])
    op.create_index("idx_facilities_latlon", "facilities", ["lat", "lon"])

    op.create_table(
        "violations",
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("facility_source_id", sa.Text, nullable=False),
        sa.Column("facility_source", sa.Text, nullable=False),
        sa.Column("violation_type", sa.Text),
        sa.Column("violation_date", sa.Text),
        sa.Column("statute", sa.Text),
        sa.Column("program_area", sa.Text),
        sa.Column("severity", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("last_updated", sa.Text, nullable=False),
        sa.UniqueConstraint("source", "source_id"),
    )
    op.create_index("idx_violations_facility", "violations", ["facility_source", "facility_source_id"])
    op.create_index("idx_violations_facility_source_id", "violations", ["facility_source_id"])
    op.create_index("idx_violations_date", "violations", ["violation_date"])
    op.create_index("idx_violations_source", "violations", ["source"])
    op.create_index("idx_violations_program_area", "violations", ["program_area"])

    op.create_table(
        "ingestion_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("started_at", sa.Text, nullable=False),
        sa.Column("completed_at", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("records_processed", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text),
    )

    op.create_table(
        "facility_scores",
        sa.Column("source_id", sa.Text, primary_key=True),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("risk_level", sa.Text, nullable=False),
        sa.Column("violation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("raw_violation_count", sa.Integer, server_default="0"),
        sa.Column("latest_violation_date", sa.Text),
        sa.Column("naics_tier", sa.Integer),
        sa.Column("program_count", sa.Integer, server_default="0"),
        sa.Column("confidence", sa.Text, server_default="low"),
        sa.Column("scored_at", sa.Text, nullable=False),
    )

    op.create_table(
        "api_keys",
        sa.Column("key_hash", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("rate_limit", sa.Integer),
    )

    op.create_table(
        "api_usage",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("api_key", sa.Text),
        sa.Column("response_time_ms", sa.Float, nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
    )
    op.create_index("idx_api_usage_timestamp", "api_usage", ["timestamp"])
    op.create_index("idx_api_usage_api_key", "api_usage", ["api_key"])

    op.create_table(
        "facility_matches",
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("canonical_id", sa.Text, nullable=False),
        sa.Column("match_tier", sa.Integer),
        sa.Column("match_score", sa.Float),
        sa.Column("matched_at", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("source", "source_id"),
    )
    op.create_index("idx_facility_matches_canonical", "facility_matches", ["canonical_id"])
    op.create_index("idx_facility_matches_source_id", "facility_matches", ["source_id"])
    op.create_index("idx_fm_source_sourceid_canonical", "facility_matches", ["source", "source_id", "canonical_id"])

    op.create_table(
        "unified_facilities",
        sa.Column("source_id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text),
        sa.Column("address", sa.Text),
        sa.Column("city", sa.Text),
        sa.Column("state", sa.Text),
        sa.Column("zip_code", sa.Text),
        sa.Column("county", sa.Text),
        sa.Column("lat", sa.Float),
        sa.Column("lon", sa.Float),
        sa.Column("naics_codes", sa.Text),
        sa.Column("sic_codes", sa.Text),
        sa.Column("programs", sa.Text),
        sa.Column("source_count", sa.Integer),
        sa.Column("sources", sa.Text),
        sa.Column("last_updated", sa.Text),
    )
    op.create_index("idx_unified_state", "unified_facilities", ["state"])
    op.create_index("idx_unified_zip", "unified_facilities", ["zip_code"])
    op.create_index("idx_unified_city", "unified_facilities", ["city"])
    op.create_index("idx_unified_latlon", "unified_facilities", ["lat", "lon"])


def downgrade() -> None:
    # Cannot downgrade past baseline.
    pass
