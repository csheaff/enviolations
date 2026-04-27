"""Add source column to api_usage for distinguishing eval vs organic traffic

Revision ID: 008_add_api_usage_source
Revises: 007_add_api_usage_params
Create Date: 2026-03-20

Adds a nullable TEXT column to api_usage for tagging traffic origin.
NULL = organic user traffic, 'eval' = evaluation agent traffic.
Sent via X-Source header from clients to tag traffic origin.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "008_add_api_usage_source"
down_revision: Union[str, None] = "007_add_api_usage_params"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_usage", sa.Column("source", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("api_usage", "source")
