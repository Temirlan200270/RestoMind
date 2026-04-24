"""menu_items: portion/allergens/upsell profile for AI decision layer

Revision ID: 20260424_menu_sell
Revises: 20260420_user_meta
Create Date: 2026-04-24

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260424_menu_sell"
down_revision: Union[str, None] = "20260420_user_meta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c.get("name") for c in insp.get_columns("menu_items")}
    except Exception:
        cols = set()

    def add(name: str, col: sa.Column) -> None:
        if name not in cols:
            op.add_column("menu_items", col)

    add(
        "portion_kind",
        sa.Column("portion_kind", sa.String(length=20), server_default="single", nullable=False),
    )
    add("serves_min", sa.Column("serves_min", sa.Integer(), server_default="1", nullable=False))
    add("serves_max", sa.Column("serves_max", sa.Integer(), server_default="1", nullable=False))
    add("allergens", sa.Column("allergens", sa.Text(), server_default="", nullable=False))
    add(
        "ingredients_summary",
        sa.Column("ingredients_summary", sa.Text(), server_default="", nullable=False),
    )
    add("dietary_tags", sa.Column("dietary_tags", sa.Text(), server_default="", nullable=False))
    add("upsell_pairs", sa.Column("upsell_pairs", sa.Text(), server_default="", nullable=False))


def downgrade() -> None:
    for name in (
        "upsell_pairs",
        "dietary_tags",
        "ingredients_summary",
        "allergens",
        "serves_max",
        "serves_min",
        "portion_kind",
    ):
        try:
            op.drop_column("menu_items", name)
        except Exception:
            pass
