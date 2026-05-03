"""staff_users.tenant_owner_id для мультифилиальности владельца сети

Revision ID: 20260504_e21_owner
Revises: 20260503_pwh_events
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260504_e21_owner"
down_revision: Union[str, None] = "20260503_pwh_events"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "staff_users",
        sa.Column(
            "tenant_owner_id",
            sa.Integer(),
            nullable=True,
            comment="Сеть (tenant id): переключатель филиалов; NULL — один филиал",
        ),
    )
    op.create_index(
        op.f("ix_staff_users_tenant_owner_id"),
        "staff_users",
        ["tenant_owner_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_staff_users_tenant_owner_id_tenants"),
        "staff_users",
        "tenants",
        ["tenant_owner_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_staff_users_tenant_owner_id_tenants"),
        "staff_users",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_staff_users_tenant_owner_id"), table_name="staff_users")
    op.drop_column("staff_users", "tenant_owner_id")
