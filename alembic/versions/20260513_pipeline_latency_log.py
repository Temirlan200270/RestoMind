"""Pipeline latency log table

Revision ID: 20260513_pipeline_latency
Revises: 20260512_p15_ai_snooze
Create Date: 2026-05-13
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260513_pipeline_latency"
down_revision: Union[str, None] = "20260512_p15_ai_snooze"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_latency_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("pipeline_type", sa.String(32), nullable=False, server_default="whatsapp_text"),
        sa.Column("dedupe_ms", sa.Integer(), nullable=True),
        sa.Column("context_ms", sa.Integer(), nullable=True),
        sa.Column("llm_ms", sa.Integer(), nullable=True),
        sa.Column("route_ms", sa.Integer(), nullable=True),
        sa.Column("reply_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_latency_logs_org_created", "pipeline_latency_logs",
                    ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_latency_logs_org_created", table_name="pipeline_latency_logs")
    op.drop_table("pipeline_latency_logs")
