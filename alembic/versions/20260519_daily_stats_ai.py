"""Add ai_messages_count and dialogs_count to daily_org_stats (Phase 5 full)

Revision ID: 20260519_daily_stats_ai
Revises: 20260519_staff_meta_json
Create Date: 2026-05-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260519_daily_stats_ai"
down_revision: Union[str, None] = "20260519_staff_meta_json"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_org_stats",
        sa.Column("ai_messages_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "daily_org_stats",
        sa.Column("dialogs_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("daily_org_stats", "dialogs_count")
    op.drop_column("daily_org_stats", "ai_messages_count")
