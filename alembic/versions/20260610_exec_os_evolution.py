"""Executive OS evolution: agent action lineage, preview, idempotency.

Revision ID: 20260610_exec_os
Revises: 20260609_tenant_rls
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260610_exec_os"
down_revision: Union[str, None] = "20260609_tenant_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_action_proposals",
        sa.Column("source_insight_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("source_snapshot_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("source_conversation_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("trace_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("preview_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("previewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
    )
    op.create_index(
        "ix_agent_action_proposals_org_idempotency",
        "agent_action_proposals",
        ["organization_id", "idempotency_key"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_agent_action_proposals_source_insight",
        "agent_action_proposals",
        "operational_insights",
        ["source_insight_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_action_proposals_source_insight", "agent_action_proposals", type_="foreignkey")
    op.drop_index("ix_agent_action_proposals_org_idempotency", table_name="agent_action_proposals")
    op.drop_column("agent_action_proposals", "idempotency_key")
    op.drop_column("agent_action_proposals", "previewed_at")
    op.drop_column("agent_action_proposals", "preview_json")
    op.drop_column("agent_action_proposals", "trace_id")
    op.drop_column("agent_action_proposals", "source_conversation_id")
    op.drop_column("agent_action_proposals", "source_snapshot_id")
    op.drop_column("agent_action_proposals", "source_insight_id")
