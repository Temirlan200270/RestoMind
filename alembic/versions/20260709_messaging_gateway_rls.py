"""messaging gateway tenant rls

Revision ID: 20260709_msg_rls
Revises: 20260709_msg_gateway
Create Date: 2026-07-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260709_msg_rls"
down_revision: Union[str, None] = "20260709_msg_gateway"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RLS_TABLES = (
    "conversations",
    "channel_connections",
    "channel_messages",
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
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(RLS_POLICY_SQL.format(table=table)))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in RLS_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
