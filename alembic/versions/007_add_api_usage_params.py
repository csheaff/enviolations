"""Add params column to api_usage for search parameter logging

Revision ID: 007_add_api_usage_params
Revises: 006_backfill_tceq_noe_statuses
Create Date: 2026-03-17

Adds a nullable TEXT column to api_usage for storing JSON-encoded query
parameters on search endpoints. Stores state (extracted from address),
radius — never full addresses.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "007_add_api_usage_params"
down_revision: Union[str, None] = "006_backfill_tceq_noe_statuses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_usage", sa.Column("params", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("api_usage", "params")
