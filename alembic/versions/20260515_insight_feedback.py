"""OperationalInsight: add was_useful + notes fields for operator feedback

Revision ID: 20260515_insight_feedback
Revises: 20260514_marketing_loyalty
Create Date: 2026-05-15
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260515_insight_feedback"
down_revision: Union[str, None] = "20260514_marketing_loyalty"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns("operational_insights")]
    if "was_useful" not in cols:
        op.add_column("operational_insights", sa.Column("was_useful", sa.Boolean(), nullable=True))
    if "notes" not in cols:
        op.add_column("operational_insights", sa.Column("notes", sa.String(500), nullable=True))


def downgrade() -> None:
    try:
        op.drop_column("operational_insights", "notes")
    except Exception:
        pass
    try:
        op.drop_column("operational_insights", "was_useful")
    except Exception:
        pass
