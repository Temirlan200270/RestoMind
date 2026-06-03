"""User session optimistic version

Revision ID: 20260603_user_session_version
Revises: 20260604_telegram_org_mapping, 20260603_menu_item_lifecycle
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_user_session_version"
down_revision: Union[str, Sequence[str], None] = (
    "20260604_telegram_org_mapping",
    "20260603_menu_item_lifecycle",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "session_version")
