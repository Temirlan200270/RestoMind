"""Upsell phrase A/B experiments (Revenue Copilot RC-C)

Revision ID: 20260601_upsell_experiments
Revises: 20260525_owner_intel_foundation
Create Date: 2026-06-01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260601_upsell_experiments"
down_revision: Union[str, None] = "20260525_owner_intel_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upsell_phrase_variants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("variant_key", sa.String(length=64), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("weight", sa.Integer(), server_default="100", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("stats_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["upsell_rules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "rule_id",
            "variant_key",
            name="uq_upsell_phrase_variant_org_rule_key",
        ),
    )
    op.create_index(
        "ix_upsell_phrase_variants_org_rule",
        "upsell_phrase_variants",
        ["organization_id", "rule_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_upsell_phrase_variants_org_rule", table_name="upsell_phrase_variants")
    op.drop_table("upsell_phrase_variants")
