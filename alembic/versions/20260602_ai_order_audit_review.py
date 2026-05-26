"""QA audit review_reason column

Revision ID: 20260602_ai_order_audit_review
Revises: 20260601_upsell_experiments
Create Date: 2026-06-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260602_ai_order_audit_review"
down_revision: Union[str, None] = "20260601_upsell_experiments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_order_audits",
        sa.Column("review_reason", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_order_audits", "review_reason")
