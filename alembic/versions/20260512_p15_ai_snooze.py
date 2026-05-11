"""P1.5 — User.ai_snoozed_until for timed AI pause (WhatsApp).

Revision ID: 20260512_p15_ai_snooze
Revises: 20260512_e23_billing_minimal
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260512_p15_ai_snooze"
down_revision: Union[str, None] = "20260512_e23_billing_minimal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "ai_snoozed_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC: до этого момента LLM не вызывается (временная пауза ИИ)",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "ai_snoozed_until")
