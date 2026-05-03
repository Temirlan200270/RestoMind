"""menu_items: embedding bytes for E12 menu RAG

Revision ID: 20260505_menu_emb
Revises: 20260504_e21_owner
Create Date: 2026-05-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260505_menu_emb"
down_revision: Union[str, None] = "20260504_e21_owner"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c.get("name") for c in insp.get_columns("menu_items")}
    except Exception:
        cols = set()
    if "embedding" not in cols:
        op.add_column(
            "menu_items",
            sa.Column("embedding", sa.LargeBinary(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c.get("name") for c in insp.get_columns("menu_items")}
    except Exception:
        cols = set()
    if "embedding" in cols:
        op.drop_column("menu_items", "embedding")
