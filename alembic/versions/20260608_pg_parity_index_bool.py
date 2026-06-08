"""Postgres parity for menu_items.is_archived boolean default.

ix_orders_org_kind already created in 20260514_night_preorders — do not recreate here.

Revision ID: 20260608_pg_parity_index_bool
Revises: 20260604_iiko_last_error_text
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260608_pg_parity_index_bool"
down_revision: Union[str, None] = "20260604_iiko_last_error_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "menu_items",
        "is_archived",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.false(),
    )


def downgrade() -> None:
    op.alter_column(
        "menu_items",
        "is_archived",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=None,
    )
