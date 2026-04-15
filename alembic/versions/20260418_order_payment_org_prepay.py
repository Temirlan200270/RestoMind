"""orders payment columns; organizations.prepayment_enforced

Revision ID: 20260418_pay
Revises: 20260417_fr
Create Date: 2026-04-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260418_pay"
down_revision: Union[str, None] = "20260417_fr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "prepayment_enforced",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.alter_column("organizations", "prepayment_enforced", server_default=None)

    op.add_column("orders", sa.Column("payment_provider", sa.String(length=64), nullable=True))
    op.add_column("orders", sa.Column("external_payment_id", sa.String(length=200), nullable=True))
    op.add_column("orders", sa.Column("payment_amount_captured", sa.Numeric(12, 2), nullable=True))
    op.create_index("ix_orders_payment_provider", "orders", ["payment_provider"])
    op.create_index("ix_orders_external_payment_id", "orders", ["external_payment_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_external_payment_id", table_name="orders")
    op.drop_index("ix_orders_payment_provider", table_name="orders")
    op.drop_column("orders", "payment_amount_captured")
    op.drop_column("orders", "external_payment_id")
    op.drop_column("orders", "payment_provider")
    op.drop_column("organizations", "prepayment_enforced")
