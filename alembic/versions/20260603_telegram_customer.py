"""Telegram customer channel foundation (Wave 4 TG)

Revision ID: 20260603_telegram_customer
Revises: 20260601_upsell_experiments
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_telegram_customer"
down_revision: Union[str, None] = "20260602_ai_order_audit_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "telegram_user_id",
            sa.BigInteger(),
            nullable=True,
            comment="Telegram user id клиента (customer channel)",
        ),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=False)
    op.create_unique_constraint(
        "uq_users_organization_telegram_user_id",
        "users",
        ["organization_id", "telegram_user_id"],
    )

    op.add_column(
        "chat_logs",
        sa.Column(
            "channel",
            sa.String(length=16),
            server_default="whatsapp",
            nullable=False,
            comment="whatsapp | telegram | operator",
        ),
    )
    op.create_index("ix_chat_logs_channel", "chat_logs", ["channel"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_logs_channel", table_name="chat_logs")
    op.drop_column("chat_logs", "channel")
    op.drop_constraint("uq_users_organization_telegram_user_id", "users", type_="unique")
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_column("users", "telegram_user_id")
