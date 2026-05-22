"""P3 Growth: waiter KPI tables (registry, daily KPI, iiko sync audit)

Revision ID: 20260523_p3_waiter_kpi
Revises: 20260521_superadmin_audit
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260523_p3_waiter_kpi"
down_revision: Union[str, None] = "20260521_superadmin_audit"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "waiter_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("waiter_iiko_id", sa.String(length=120), nullable=False),
        sa.Column("waiter_name", sa.String(length=240), server_default="", nullable=False),
        sa.Column("source", sa.String(length=32), server_default="cloud_delivery", nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "waiter_iiko_id", name="uq_waiter_registry_org_waiter"),
    )
    op.create_index(
        op.f("ix_waiter_registry_organization_id"),
        "waiter_registry",
        ["organization_id"],
    )
    op.create_index(
        "ix_waiter_registry_org_seen",
        "waiter_registry",
        ["organization_id", "last_seen_at"],
    )

    op.create_table(
        "waiter_kpi_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("kpi_date", sa.Date(), nullable=False),
        sa.Column("waiter_iiko_id", sa.String(length=120), nullable=False),
        sa.Column("orders_served", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_revenue_kzt", sa.Numeric(precision=14, scale=2), server_default="0", nullable=False),
        sa.Column("avg_check_kzt", sa.Numeric(precision=14, scale=2), server_default="0", nullable=False),
        sa.Column("guests_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancelled_orders", sa.Integer(), server_default="0", nullable=False),
        sa.Column("avg_service_time_min", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("items_sold_json", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "kpi_date",
            "waiter_iiko_id",
            name="uq_waiter_kpi_org_date_waiter",
        ),
    )
    op.create_index(
        op.f("ix_waiter_kpi_daily_organization_id"),
        "waiter_kpi_daily",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_waiter_kpi_daily_location_id"),
        "waiter_kpi_daily",
        ["location_id"],
    )
    op.create_index(
        "ix_waiter_kpi_org_date",
        "waiter_kpi_daily",
        ["organization_id", "kpi_date"],
    )

    op.create_table(
        "iiko_sync_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("sync_kind", sa.String(length=40), server_default="waiter_kpi", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="ok", nullable=False),
        sa.Column("rows_upserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_iiko_sync_runs_organization_id"),
        "iiko_sync_runs",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_iiko_sync_runs_finished_at"),
        "iiko_sync_runs",
        ["finished_at"],
    )
    op.create_index(
        "ix_iiko_sync_runs_org_kind_finished",
        "iiko_sync_runs",
        ["organization_id", "sync_kind", "finished_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_iiko_sync_runs_org_kind_finished", table_name="iiko_sync_runs")
    op.drop_index(op.f("ix_iiko_sync_runs_finished_at"), table_name="iiko_sync_runs")
    op.drop_index(op.f("ix_iiko_sync_runs_organization_id"), table_name="iiko_sync_runs")
    op.drop_table("iiko_sync_runs")

    op.drop_index("ix_waiter_kpi_org_date", table_name="waiter_kpi_daily")
    op.drop_index(op.f("ix_waiter_kpi_daily_location_id"), table_name="waiter_kpi_daily")
    op.drop_index(op.f("ix_waiter_kpi_daily_organization_id"), table_name="waiter_kpi_daily")
    op.drop_table("waiter_kpi_daily")

    op.drop_index("ix_waiter_registry_org_seen", table_name="waiter_registry")
    op.drop_index(op.f("ix_waiter_registry_organization_id"), table_name="waiter_registry")
    op.drop_table("waiter_registry")
