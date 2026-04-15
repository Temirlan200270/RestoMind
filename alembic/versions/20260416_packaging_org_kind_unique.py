"""packaging_rules: organization_id NOT NULL, unique (organization_id, kind)

Revision ID: 20260416_pkg
Revises: 20260415_mt
Create Date: 2026-04-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260416_pkg"
down_revision: Union[str, None] = "20260415_mt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.execute(
        sa.text(
            "UPDATE packaging_rules SET organization_id = "
            "(SELECT id FROM organizations ORDER BY id ASC LIMIT 1) "
            "WHERE organization_id IS NULL",
        ),
    )

    if is_sqlite:
        with op.batch_alter_table("packaging_rules", schema=None, recreate="always") as batch_op:
            batch_op.alter_column(
                "organization_id",
                existing_type=sa.Integer(),
                nullable=False,
            )
            batch_op.create_unique_constraint(
                "uq_packaging_rules_org_kind",
                ["organization_id", "kind"],
            )
    else:
        op.execute(sa.text("ALTER TABLE packaging_rules DROP CONSTRAINT IF EXISTS packaging_rules_kind_key"))
        op.alter_column(
            "packaging_rules",
            "organization_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        op.create_unique_constraint(
            "uq_packaging_rules_org_kind",
            "packaging_rules",
            ["organization_id", "kind"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        with op.batch_alter_table("packaging_rules", schema=None, recreate="always") as batch_op:
            batch_op.drop_constraint("uq_packaging_rules_org_kind", type_="unique")
            batch_op.create_unique_constraint("packaging_rules_kind_key", ["kind"])
            batch_op.alter_column(
                "organization_id",
                existing_type=sa.Integer(),
                nullable=True,
            )
    else:
        op.drop_constraint("uq_packaging_rules_org_kind", "packaging_rules", type_="unique")
        op.create_unique_constraint("packaging_rules_kind_key", "packaging_rules", ["kind"])
        op.alter_column(
            "packaging_rules",
            "organization_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
