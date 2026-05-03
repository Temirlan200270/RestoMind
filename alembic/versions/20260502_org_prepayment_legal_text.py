"""organizations prepayment_legal_text

Revision ID: 20260502_prepay_legal
Revises: 20260428_auto_iiko
Create Date: 2026-05-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260502_prepay_legal"
down_revision: Union[str, None] = "20260428_auto_iiko"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "prepayment_legal_text",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "prepayment_legal_text")
