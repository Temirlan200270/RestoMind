"""Telegram org mapping — bot username + webhook secret per tenant

Revision ID: 20260604_telegram_org_mapping
Revises: 20260603_pos_provider
Create Date: 2026-06-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260604_telegram_org_mapping"
down_revision: Union[str, None] = "20260603_pos_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "telegram_bot_username",
            sa.String(length=64),
            nullable=True,
            comment="@username Telegram-бота клиентского канала",
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "telegram_webhook_secret",
            sa.String(length=128),
            nullable=True,
            comment="X-Telegram-Bot-Api-Secret-Token / fingerprint bot token для маршрутизации webhook",
        ),
    )
    op.create_index(
        "ix_organizations_telegram_webhook_secret",
        "organizations",
        ["telegram_webhook_secret"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_organizations_telegram_webhook_secret", table_name="organizations")
    op.drop_column("organizations", "telegram_webhook_secret")
    op.drop_column("organizations", "telegram_bot_username")
