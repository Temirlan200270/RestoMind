"""Phase 1.1: locations table + location_id on orders/chats/bookings

Revision ID: 20260520_locations_phase11
Revises: 20260519_audit_log
Create Date: 2026-05-20
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260520_locations_phase11"
down_revision: Union[str, None] = "20260519_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(80), server_default="main", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_locations_org_slug"),
    )
    op.create_index("ix_locations_organization_id", "locations", ["organization_id"])

    for table in ("orders", "chat_logs", "bookings"):
        op.add_column(table, sa.Column("location_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_location_id",
            table,
            "locations",
            ["location_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table}_location_id", table, ["location_id"])


def downgrade() -> None:
    for table in ("bookings", "chat_logs", "orders"):
        op.drop_index(f"ix_{table}_location_id", table_name=table)
        op.drop_constraint(f"fk_{table}_location_id", table, type_="foreignkey")
        op.drop_column(table, "location_id")
    op.drop_index("ix_locations_organization_id", table_name="locations")
    op.drop_table("locations")
