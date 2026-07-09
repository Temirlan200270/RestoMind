"""default outbound channel connection

Revision ID: 20260709_default_channel
Revises: 20260709_msg_rls
Create Date: 2026-07-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260709_default_channel"
down_revision: Union[str, None] = "20260709_msg_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channel_connections",
        sa.Column("is_default_outbound", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_index(
            "uq_default_channel_per_org",
            "channel_connections",
            ["organization_id"],
            unique=True,
            postgresql_where=sa.text("is_default_outbound = true"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("uq_default_channel_per_org", table_name="channel_connections")
    op.drop_column("channel_connections", "is_default_outbound")
