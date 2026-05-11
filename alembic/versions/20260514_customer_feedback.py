"""Customer feedback table + review_url_2gis on organizations

Revision ID: 20260514_customer_feedback
Revises: 20260514_night_preorders
Create Date: 2026-05-14
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260514_customer_feedback"
down_revision: Union[str, None] = "20260514_night_preorders"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # review_url_2gis on organizations
    org_cols = [c["name"] for c in insp.get_columns("organizations")]
    if "review_url_2gis" not in org_cols:
        op.add_column("organizations", sa.Column("review_url_2gis", sa.String(512), nullable=True))

    # customer_feedback table
    if not insp.has_table("customer_feedback"):
        op.create_table(
            "customer_feedback",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("order_id", sa.Integer(), nullable=True),
            sa.Column("rating", sa.String(20), nullable=False),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_customer_feedback_org_created", "customer_feedback",
                        ["organization_id", "created_at"])
        op.create_index("ix_customer_feedback_org", "customer_feedback", ["organization_id"])


def downgrade() -> None:
    try:
        op.drop_index("ix_customer_feedback_org", table_name="customer_feedback")
        op.drop_index("ix_customer_feedback_org_created", table_name="customer_feedback")
        op.drop_table("customer_feedback")
    except Exception:
        pass
    try:
        op.drop_column("organizations", "review_url_2gis")
    except Exception:
        pass
