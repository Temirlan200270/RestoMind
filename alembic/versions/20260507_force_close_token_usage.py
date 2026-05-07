"""Force close fields + AI token usage table

Revision ID: 20260507_force_close_tokens
Revises: 20260507_ui_u45_packaging
Create Date: 2026-05-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260507_force_close_tokens"
down_revision: Union[str, None] = "20260507_ui_u45_packaging"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Экстренное закрытие заведения
    op.add_column(
        "organizations",
        sa.Column(
            "force_closed_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Экстренное закрытие: заведение закрыто до этого момента (UTC)",
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "force_closed_reason",
            sa.String(255),
            nullable=False,
            server_default="",
            comment="Причина экстренного закрытия",
        ),
    )

    # Таблица для учёта токенов AI по организации/дню
    op.create_table(
        "ai_usage_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False, comment="Дата (UTC) агрегации"),
        sa.Column("provider", sa.String(32), nullable=False, server_default="openai", comment="openai | gemini"),
        sa.Column("model", sa.String(64), nullable=False, server_default="", comment="Идентификатор модели"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0", comment="Количество вызовов за день"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index(
        "ix_ai_usage_logs_org_day",
        "ai_usage_logs",
        ["organization_id", "day"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_logs_org_day", table_name="ai_usage_logs")
    op.drop_table("ai_usage_logs")
    op.drop_column("organizations", "force_closed_reason")
    op.drop_column("organizations", "force_closed_until")
