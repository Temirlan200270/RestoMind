"""Payment infrastructure: PaymentTransaction + OrganizationPaymentConfig

Revision ID: 20260509_payment_tx_config
Revises: 20260508_intelligence_ops
Create Date: 2026-05-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260509_payment_tx_config"
down_revision: Union[str, None] = "20260508_intelligence_ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_payment_id", sa.String(200), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="KZT"),
        sa.Column("payment_url", sa.String(1024), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(512), nullable=True),
        sa.Column("provider_payload_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_tx_idempotency_key"),
    )
    op.create_index(
        "ix_payment_tx_order_id", "payment_transactions", ["order_id"]
    )
    op.create_index(
        "ix_payment_tx_org_status",
        "payment_transactions",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_payment_transactions_provider_payment_id",
        "payment_transactions",
        ["provider_payment_id"],
    )

    op.create_table(
        "organization_payment_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("merchant_id", sa.String(200), nullable=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("encrypted_secret_key", sa.Text(), nullable=True),
        sa.Column("encrypted_public_key", sa.Text(), nullable=True),
        sa.Column("extra_config_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "provider", name="uq_org_payment_config_provider"
        ),
    )
    op.create_index(
        "ix_org_payment_configs_org_id",
        "organization_payment_configs",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_table("organization_payment_configs")
    op.drop_index("ix_payment_transactions_provider_payment_id", "payment_transactions")
    op.drop_index("ix_payment_tx_org_status", "payment_transactions")
    op.drop_index("ix_payment_tx_order_id", "payment_transactions")
    op.drop_table("payment_transactions")
