"""Message accounting log table for WhatsApp message telemetry

Revision ID: 20260515_message_accounting
Revises: 20260515_insight_feedback
Create Date: 2026-05-15
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260515_message_accounting"
down_revision: Union[str, None] = "20260515_insight_feedback"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "message_accounting_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("message_type", sa.String(40), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "day", "direction", "source", "message_type",
            name="uq_msg_acct_org_day_dir_src_type",
        ),
    )
    op.create_index("ix_msg_acct_org_day", "message_accounting_logs", ["organization_id", "day"])
    op.create_index("ix_msg_acct_org_id", "message_accounting_logs", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_msg_acct_org_id", table_name="message_accounting_logs")
    op.drop_index("ix_msg_acct_org_day", table_name="message_accounting_logs")
    op.drop_table("message_accounting_logs")
