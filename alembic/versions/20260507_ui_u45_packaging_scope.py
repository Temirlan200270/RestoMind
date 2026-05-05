"""UI U4.5 packaging rule scopes

Revision ID: 20260507_ui_u45_packaging
Revises: 20260506_org_int_sync
Create Date: 2026-05-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260507_ui_u45_packaging"
down_revision: Union[str, None] = "20260506_org_int_sync"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("packaging_rules", schema=None) as batch_op:
        batch_op.add_column(sa.Column("scope", sa.String(length=20), nullable=False, server_default="item"))
        batch_op.add_column(sa.Column("category_match", sa.String(length=120), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("packaging_rules", schema=None) as batch_op:
        batch_op.drop_column("category_match")
        batch_op.drop_column("scope")
