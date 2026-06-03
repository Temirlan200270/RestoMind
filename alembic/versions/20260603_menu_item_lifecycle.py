"""Menu item lifecycle for iiko replace/prune sync

Revision ID: 20260603_menu_item_lifecycle
Revises: 20260603_intelos_lineage
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_menu_item_lifecycle"
down_revision: Union[str, None] = "20260603_intelos_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column("source", sa.String(length=24), server_default="legacy", nullable=False),
    )
    op.add_column("menu_items", sa.Column("last_seen_iiko_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("menu_items", sa.Column("is_archived", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("menu_items", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_menu_items_source", "menu_items", ["source"])
    op.create_index("ix_menu_items_last_seen_iiko_sync_at", "menu_items", ["last_seen_iiko_sync_at"])
    op.create_index("ix_menu_items_is_archived", "menu_items", ["is_archived"])


def downgrade() -> None:
    op.drop_index("ix_menu_items_is_archived", table_name="menu_items")
    op.drop_index("ix_menu_items_last_seen_iiko_sync_at", table_name="menu_items")
    op.drop_index("ix_menu_items_source", table_name="menu_items")
    op.drop_column("menu_items", "archived_at")
    op.drop_column("menu_items", "is_archived")
    op.drop_column("menu_items", "last_seen_iiko_sync_at")
    op.drop_column("menu_items", "source")
