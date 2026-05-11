"""Marketing blasts, loyalty tables + users.marketing_opt_out

Revision ID: 20260514_marketing_loyalty
Revises: 20260514_customer_feedback
Create Date: 2026-05-14
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260514_marketing_loyalty"
down_revision: Union[str, None] = "20260514_customer_feedback"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # users.marketing_opt_out
    user_cols = [c["name"] for c in insp.get_columns("users")]
    if "marketing_opt_out" not in user_cols:
        op.add_column("users", sa.Column("marketing_opt_out", sa.Boolean(), nullable=False, server_default="false"))

    # marketing_blasts
    if not insp.has_table("marketing_blasts"):
        op.create_table(
            "marketing_blasts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("segment_type", sa.String(50), nullable=False),
            sa.Column("message_text", sa.Text(), nullable=False),
            sa.Column("template_name", sa.String(120), nullable=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("total_recipients", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_marketing_blasts_org_status", "marketing_blasts", ["organization_id", "status"])

    # marketing_blast_recipients
    if not insp.has_table("marketing_blast_recipients"):
        op.create_table(
            "marketing_blast_recipients",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("blast_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("phone", sa.String(20), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["blast_id"], ["marketing_blasts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_mbr_blast_status", "marketing_blast_recipients", ["blast_id", "status"])

    # loyalty_balance
    if not insp.has_table("loyalty_balance"):
        op.create_table(
            "loyalty_balance",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("balance_points", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "user_id", name="uq_loyalty_balance_org_user"),
        )

    # loyalty_transactions
    if not insp.has_table("loyalty_transactions"):
        op.create_table(
            "loyalty_transactions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=True),
            sa.Column("points", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("note", sa.String(512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_loyalty_tx_org_user_created", "loyalty_transactions",
                        ["organization_id", "user_id", "created_at"])


def downgrade() -> None:
    for tbl in ["loyalty_transactions", "loyalty_balance",
                "marketing_blast_recipients", "marketing_blasts"]:
        try:
            op.drop_table(tbl)
        except Exception:
            pass
    try:
        op.drop_column("users", "marketing_opt_out")
    except Exception:
        pass
