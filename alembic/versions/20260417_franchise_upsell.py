"""tenants, organizations iiko enc + terminal + tenant_id, upsell_rules

Revision ID: 20260417_fr
Revises: 20260416_pkg
Create Date: 2026-04-17

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260417_fr"
down_revision: Union[str, None] = "20260416_pkg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("plan", sa.String(length=64), server_default="standard", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute(
        sa.text(
            "INSERT INTO tenants (name, plan) SELECT 'Default', 'standard' "
            "WHERE NOT EXISTS (SELECT 1 FROM tenants LIMIT 1)",
        ),
    )

    op.add_column(
        "organizations",
        sa.Column("tenant_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_organizations_tenant_id",
        "organizations",
        "tenants",
        ["tenant_id"],
        ["id"],
    )
    op.create_index(op.f("ix_organizations_tenant_id"), "organizations", ["tenant_id"], unique=False)

    op.execute(
        sa.text(
            "UPDATE organizations SET tenant_id = (SELECT id FROM tenants ORDER BY id ASC LIMIT 1) "
            "WHERE tenant_id IS NULL",
        ),
    )

    op.add_column(
        "organizations",
        sa.Column("iiko_api_login_enc", sa.Text(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "iiko_terminal_group_id",
            sa.String(length=255),
            server_default="",
            nullable=False,
        ),
    )


    op.create_table(
        "upsell_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("trigger_mode", sa.String(length=32), server_default="missing_category", nullable=False),
        sa.Column("trigger_category", sa.String(length=120), server_default="", nullable=False),
        sa.Column("suggest_category", sa.String(length=120), server_default="", nullable=False),
        sa.Column("min_order_sum", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False),
        sa.Column("max_order_sum", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("phrase_template", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_upsell_rules_organization_id"), "upsell_rules", ["organization_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_upsell_rules_organization_id"), table_name="upsell_rules")
    op.drop_table("upsell_rules")
    op.drop_column("organizations", "iiko_terminal_group_id")
    op.drop_column("organizations", "iiko_api_login_enc")
    op.drop_constraint("fk_organizations_tenant_id", "organizations", type_="foreignkey")
    op.drop_index(op.f("ix_organizations_tenant_id"), table_name="organizations")
    op.drop_column("organizations", "tenant_id")
    op.drop_table("tenants")
