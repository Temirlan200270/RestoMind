"""org auto_send_to_iiko_after_payment

Revision ID: 20260428_auto_iiko
Revises: 20260427_org_schedule
Create Date: 2026-04-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260428_auto_iiko"
down_revision: Union[str, None] = "20260427_org_schedule"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "auto_send_to_iiko_after_payment",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "auto_send_to_iiko_after_payment")
