"""iiko Office inventory sync: integration_config_json + inventory sync status

Revision ID: 20260522_iiko_office_inventory
Revises: 20260521_final_mile
Create Date: 2026-05-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260522_iiko_office_inventory"
down_revision: Union[str, None] = "20260521_final_mile"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("integration_config_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "organization_integration_sync",
        sa.Column("last_inventory_sync_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organization_integration_sync",
        sa.Column("last_inventory_sync_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "organization_integration_sync",
        sa.Column("last_inventory_sync_error", sa.Text(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_column("organization_integration_sync", "last_inventory_sync_error")
    op.drop_column("organization_integration_sync", "last_inventory_sync_ok")
    op.drop_column("organization_integration_sync", "last_inventory_sync_at")
    op.drop_column("organizations", "integration_config_json")
