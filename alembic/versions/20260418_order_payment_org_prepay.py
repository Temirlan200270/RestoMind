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
    bind = op.get_bind()
    insp = sa.inspect(bind)

    def _cols(table: str) -> set[str]:
        try:
            return {c.get("name") for c in insp.get_columns(table)}
        except Exception:
            return set()

    def _has_index(table: str, name: str) -> bool:
        try:
            return any((i.get("name") == name) for i in insp.get_indexes(table))
        except Exception:
            return False

    org_cols = _cols("organizations")
    if "prepayment_enforced" not in org_cols:
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

    order_cols = _cols("orders")
    if "payment_provider" not in order_cols:
        op.add_column("orders", sa.Column("payment_provider", sa.String(length=64), nullable=True))
    if "external_payment_id" not in order_cols:
        op.add_column("orders", sa.Column("external_payment_id", sa.String(length=200), nullable=True))
    if "payment_amount_captured" not in order_cols:
        op.add_column("orders", sa.Column("payment_amount_captured", sa.Numeric(12, 2), nullable=True))

    if not _has_index("orders", "ix_orders_payment_provider"):
        op.create_index("ix_orders_payment_provider", "orders", ["payment_provider"])
    if not _has_index("orders", "ix_orders_external_payment_id"):
        op.create_index("ix_orders_external_payment_id", "orders", ["external_payment_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_external_payment_id", table_name="orders")
    op.drop_index("ix_orders_payment_provider", table_name="orders")
    op.drop_column("orders", "payment_amount_captured")
    op.drop_column("orders", "external_payment_id")
    op.drop_column("orders", "payment_provider")
    op.drop_column("organizations", "prepayment_enforced")
