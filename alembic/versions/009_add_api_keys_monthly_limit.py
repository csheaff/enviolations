"""Add monthly_limit column to api_keys for monthly call caps

Revision ID: 009_add_api_keys_monthly_limit
Revises: 008_add_api_usage_source
Create Date: 2026-03-23

Adds a nullable INTEGER column to api_keys for per-key monthly call caps.
NULL = no monthly limit (e.g. internal/admin keys).
Default for paid tier is 5,000 calls/month (enforced in middleware, not here).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "009_add_api_keys_monthly_limit"
down_revision: Union[str, None] = "008_add_api_usage_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("monthly_limit", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_column("monthly_limit")
