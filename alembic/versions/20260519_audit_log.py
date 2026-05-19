"""Add audit_log table for immutable business event trail (Phase 5 OS)

Revision ID: 20260519_audit_log
Revises: 20260519_daily_stats_ai
Create Date: 2026-05-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260519_audit_log"
down_revision: Union[str, None] = "20260519_daily_stats_ai"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(50), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", sa.String(200), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_org_created", "audit_log", ["organization_id", "created_at"])
    op.create_index("ix_audit_log_org_action", "audit_log", ["organization_id", "action"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_org_action", table_name="audit_log")
    op.drop_index("ix_audit_log_org_created", table_name="audit_log")
    op.drop_table("audit_log")
