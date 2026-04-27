"""superadmin foundation: flags and registration requests

Revision ID: 20260427_superadmin
Revises: 20260424_menu_sell
Create Date: 2026-04-27

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260427_superadmin"
down_revision: Union[str, None] = "20260424_menu_sell"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    try:
        org_cols = {c.get("name") for c in insp.get_columns("organizations")}
    except Exception:
        org_cols = set()
    if "is_demo" not in org_cols:
        op.add_column(
            "organizations",
            sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    try:
        staff_cols = {c.get("name") for c in insp.get_columns("staff_users")}
    except Exception:
        staff_cols = set()
    if "is_superadmin" not in staff_cols:
        op.add_column(
            "staff_users",
            sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    try:
        tables = set(insp.get_table_names())
    except Exception:
        tables = set()
    if "registration_requests" not in tables:
        op.create_table(
            "registration_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("restaurant_name", sa.String(length=255), nullable=False),
            sa.Column("contact_name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("phone", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("has_iiko", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_by_staff_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["decided_by_staff_id"], ["staff_users.id"]),
        )
        op.create_index("ix_registration_requests_status", "registration_requests", ["status"], unique=False)
        op.create_index(
            "ix_registration_requests_decided_by_staff_id",
            "registration_requests",
            ["decided_by_staff_id"],
            unique=False,
        )
        op.create_index("ix_registration_requests_created_at", "registration_requests", ["created_at"], unique=False)


def downgrade() -> None:
    for idx_name in (
        "ix_registration_requests_created_at",
        "ix_registration_requests_decided_by_staff_id",
        "ix_registration_requests_status",
    ):
        try:
            op.drop_index(idx_name, table_name="registration_requests")
        except Exception:
            pass
    try:
        op.drop_table("registration_requests")
    except Exception:
        pass

    try:
        op.drop_column("staff_users", "is_superadmin")
    except Exception:
        pass
    try:
        op.drop_column("organizations", "is_demo")
    except Exception:
        pass
