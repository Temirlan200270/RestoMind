"""Add superadmin_audit_log table for platform-level Super Admin actions

Revision ID: 20260521_superadmin_audit
Revises: 20260522_iiko_office_inventory
Create Date: 2026-05-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260521_superadmin_audit"
down_revision: Union[str, None] = "20260522_iiko_office_inventory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "superadmin_audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_staff_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_email", sa.String(length=255), server_default="", nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=100), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_staff_user_id"], ["staff_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_superadmin_audit_created", "superadmin_audit_log", ["created_at"])
    op.create_index(
        "ix_superadmin_audit_org_created",
        "superadmin_audit_log",
        ["organization_id", "created_at"],
    )
    op.create_index(
        op.f("ix_superadmin_audit_log_actor_staff_user_id"),
        "superadmin_audit_log",
        ["actor_staff_user_id"],
    )
    op.create_index(
        op.f("ix_superadmin_audit_log_organization_id"),
        "superadmin_audit_log",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_superadmin_audit_log_organization_id"), table_name="superadmin_audit_log")
    op.drop_index(op.f("ix_superadmin_audit_log_actor_staff_user_id"), table_name="superadmin_audit_log")
    op.drop_index("ix_superadmin_audit_org_created", table_name="superadmin_audit_log")
    op.drop_index("ix_superadmin_audit_created", table_name="superadmin_audit_log")
    op.drop_table("superadmin_audit_log")
