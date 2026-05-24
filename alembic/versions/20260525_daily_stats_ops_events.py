"""Add ops/integration counters to daily_org_stats

Revision ID: 20260525_daily_stats_ops_events
Revises: 20260524_money_layer_recovered
Create Date: 2026-05-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260525_daily_stats_ops_events"
down_revision: Union[str, None] = "20260524_money_layer_recovered"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPS_COLUMNS = (
    ("pricing_adjustments", sa.Integer()),
    ("sla_violations", sa.Integer()),
    ("healing_wa_sent", sa.Integer()),
    ("draft_recovery_sent", sa.Integer()),
    ("whatsapp_delivery_failed", sa.Integer()),
)


def upgrade() -> None:
    for name, col_type in _OPS_COLUMNS:
        op.add_column(
            "daily_org_stats",
            sa.Column(name, col_type, nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for name, _ in reversed(_OPS_COLUMNS):
        op.drop_column("daily_org_stats", name)
