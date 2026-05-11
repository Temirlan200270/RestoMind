"""Night preorders: add kind column to orders

Revision ID: 20260514_night_preorders
Revises: 20260513_biz_recommendations
Create Date: 2026-05-14
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260514_night_preorders"
down_revision: Union[str, None] = "20260513_biz_recommendations"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns("orders")]
    if "kind" not in cols:
        op.add_column("orders", sa.Column("kind", sa.String(30), nullable=False, server_default="regular"))
    try:
        op.create_index("ix_orders_org_kind", "orders", ["organization_id", "kind"])
    except Exception:
        pass


def downgrade() -> None:
    try:
        op.drop_index("ix_orders_org_kind", table_name="orders")
    except Exception:
        pass
    try:
        op.drop_column("orders", "kind")
    except Exception:
        pass
