"""E2.3 — минимальный биллинг: plan_status, billing_usage_daily, rollup.

Revision ID: 20260512_e23_billing_minimal
Revises: 20260511_e22_branding
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260512_e23_billing_minimal"
down_revision: Union[str, None] = "20260511_e22_branding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "plan_status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
            comment="active | suspended — блок входа админки и входящих WhatsApp для сети",
        ),
    )

    op.create_table(
        "billing_usage_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False, comment="UTC-календарный день агрегации"),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Сумма total_tokens из ai_usage_logs по филиалам tenant",
        ),
        sa.Column(
            "ai_calls",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Сумма call_count из ai_usage_logs",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "day", name="uq_billing_usage_daily_tenant_day"),
    )
    op.create_index(
        op.f("ix_billing_usage_daily_tenant_id"),
        "billing_usage_daily",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_billing_usage_daily_tenant_id"), table_name="billing_usage_daily")
    op.drop_table("billing_usage_daily")
    op.drop_column("tenants", "plan_status")
