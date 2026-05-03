"""Per-organization iiko sync status (menu + stoplist indicators)

Revision ID: 20260506_org_int_sync
Revises: 20260505_menu_emb
Create Date: 2026-05-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260506_org_int_sync"
down_revision: Union[str, None] = "20260505_menu_emb"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_integration_sync",
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("last_stoplist_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_stoplist_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_stoplist_error", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("last_menu_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_menu_sync_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_menu_sync_error", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("organization_id"),
    )


def downgrade() -> None:
    op.drop_table("organization_integration_sync")
