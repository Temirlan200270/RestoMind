"""payment_webhook_events audit table

Revision ID: 20260503_pwh_events
Revises: 20260502_prepay_legal
Create Date: 2026-05-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260503_pwh_events"
down_revision: Union[str, None] = "20260502_prepay_legal"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_webhook_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("provider_slug", sa.String(length=64), nullable=False),
        sa.Column("external_payment_id", sa.String(length=200), nullable=True),
        sa.Column("signature_header", sa.String(length=512), nullable=True),
        sa.Column("http_headers_json", sa.JSON(), nullable=True),
        sa.Column("payload_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("payload_text", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verify_error", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("parsed_status", sa.String(length=20), nullable=True),
        sa.Column("parsed_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payment_event_id", sa.Integer(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_event_id"], ["payment_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_webhook_events_organization_id"),
        "payment_webhook_events",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_webhook_events_order_id"),
        "payment_webhook_events",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_webhook_events_provider_slug"),
        "payment_webhook_events",
        ["provider_slug"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_webhook_events_external_payment_id"),
        "payment_webhook_events",
        ["external_payment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_webhook_events_received_at"),
        "payment_webhook_events",
        ["received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_webhook_events_received_at"), table_name="payment_webhook_events")
    op.drop_index(op.f("ix_payment_webhook_events_external_payment_id"), table_name="payment_webhook_events")
    op.drop_index(op.f("ix_payment_webhook_events_provider_slug"), table_name="payment_webhook_events")
    op.drop_index(op.f("ix_payment_webhook_events_order_id"), table_name="payment_webhook_events")
    op.drop_index(op.f("ix_payment_webhook_events_organization_id"), table_name="payment_webhook_events")
    op.drop_table("payment_webhook_events")
