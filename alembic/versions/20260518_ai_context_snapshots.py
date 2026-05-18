"""AI Context Snapshot table for LLM call replay and audit

Revision ID: 20260518_ai_context_snapshots
Revises: 20260515_message_accounting
Create Date: 2026-05-18
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260518_ai_context_snapshots"
down_revision: Union[str, None] = "20260515_message_accounting"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_context_snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("business_state", sa.JSON(), nullable=True),
        sa.Column("customer_state", sa.JSON(), nullable=True),
        sa.Column("event_slice", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_ctx_snapshots_org_created",
        "ai_context_snapshots",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_ai_ctx_snapshots_org_phone",
        "ai_context_snapshots",
        ["organization_id", "phone"],
    )
    op.create_index(
        "ix_ai_ctx_snapshots_org_id",
        "ai_context_snapshots",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_ctx_snapshots_org_id", table_name="ai_context_snapshots")
    op.drop_index("ix_ai_ctx_snapshots_org_phone", table_name="ai_context_snapshots")
    op.drop_index("ix_ai_ctx_snapshots_org_created", table_name="ai_context_snapshots")
    op.drop_table("ai_context_snapshots")
