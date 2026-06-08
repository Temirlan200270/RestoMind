"""Tenant RLS policies + agent action proposals (human-in-the-loop).

Revision ID: 20260609_tenant_rls
Revises: 20260608_pg_parity_index_bool
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260609_tenant_rls"
down_revision: Union[str, None] = "20260608_pg_parity_index_bool"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RLS_TABLES = (
    "orders",
    "users",
    "menu_items",
    "system_events",
    "operational_insights",
    "bookings",
    "upsell_rules",
)

RLS_POLICY_SQL = """
CREATE POLICY tenant_isolation_{table} ON {table}
FOR ALL
USING (
    coalesce(current_setting('app.bypass_rls', true), '') = 'true'
    OR organization_id = NULLIF(current_setting('app.organization_id', true), '')::integer
)
WITH CHECK (
    coalesce(current_setting('app.bypass_rls', true), '') = 'true'
    OR organization_id = NULLIF(current_setting('app.organization_id', true), '')::integer
);
"""


def upgrade() -> None:
    op.create_table(
        "agent_action_proposals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("staff_user_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="proposed"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="hub"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_action_proposals_org_status_created",
        "agent_action_proposals",
        ["organization_id", "status", "created_at"],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(RLS_POLICY_SQL.format(table=table)))

    op.execute(sa.text("ALTER TABLE agent_action_proposals ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE agent_action_proposals FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation_agent_action_proposals ON agent_action_proposals
            FOR ALL
            USING (
                coalesce(current_setting('app.bypass_rls', true), '') = 'true'
                OR organization_id = NULLIF(current_setting('app.organization_id', true), '')::integer
            )
            WITH CHECK (
                coalesce(current_setting('app.bypass_rls', true), '') = 'true'
                OR organization_id = NULLIF(current_setting('app.organization_id', true), '')::integer
            );
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in (*RLS_TABLES, "agent_action_proposals"):
            op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
            op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_agent_action_proposals_org_status_created", table_name="agent_action_proposals")
    op.drop_table("agent_action_proposals")
