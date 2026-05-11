"""Business recommendations table

Revision ID: 20260513_biz_recommendations
Revises: 20260513_ai_usage_errors
Create Date: 2026-05-13
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260513_biz_recommendations"
down_revision: Union[str, None] = "20260513_ai_usage_errors"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "business_recommendations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("recommendation_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence_pct", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("expected_impact_kzt", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("data_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_business_recommendations_org_created", "business_recommendations",
                    ["organization_id", "created_at"])
    op.create_index("ix_business_recommendations_org_status", "business_recommendations",
                    ["organization_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_business_recommendations_org_status", table_name="business_recommendations")
    op.drop_index("ix_business_recommendations_org_created", table_name="business_recommendations")
    op.drop_table("business_recommendations")
