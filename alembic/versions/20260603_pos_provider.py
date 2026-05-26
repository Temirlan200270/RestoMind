"""POS provider column for adapter registry (Wave 4 POS)

Revision ID: 20260603_pos_provider
Revises: 20260603_telegram_customer
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_pos_provider"
down_revision: Union[str, None] = "20260603_telegram_customer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "pos_provider",
            sa.String(length=32),
            server_default="iiko",
            nullable=False,
            comment="POS adapter slug: iiko | …",
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "pos_provider")
