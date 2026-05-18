"""Add max_discount_pct to organizations (Decision Engine Phase 4)

Revision ID: 20260518_org_max_discount
Revises: 20260518_ai_context_snapshots
Create Date: 2026-05-18
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260518_org_max_discount"
down_revision: Union[str, None] = "20260518_ai_context_snapshots"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "max_discount_pct",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Decision Engine: максимальный % скидки (0 = запрещено)",
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "max_discount_pct")
