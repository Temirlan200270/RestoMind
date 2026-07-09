"""messaging gateway mvp

Revision ID: 20260709_msg_gateway
Revises: 20260610_exec_os
Create Date: 2026-07-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260709_msg_gateway"
down_revision: Union[str, None] = "20260610_exec_os"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("active_order_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["active_order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "customer_id", "status", name="uq_conversations_org_customer_status"),
    )
    op.create_index(op.f("ix_conversations_customer_id"), "conversations", ["customer_id"], unique=False)
    op.create_index(op.f("ix_conversations_organization_id"), "conversations", ["organization_id"], unique=False)

    op.create_table(
        "channel_connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="qr_required", nullable=False),
        sa.Column("external_account_id", sa.String(length=128), server_default="", nullable=False),
        sa.Column("phone", sa.String(length=32), server_default="", nullable=False),
        sa.Column("display_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("session_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("last_qr", sa.Text(), server_default="", nullable=False),
        sa.Column("health_json", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), server_default="", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            "external_account_id",
            name="uq_channel_connections_org_provider_external",
        ),
    )
    op.create_index(op.f("ix_channel_connections_organization_id"), "channel_connections", ["organization_id"], unique=False)
    op.create_index(op.f("ix_channel_connections_provider"), "channel_connections", ["provider"], unique=False)
    op.create_index(op.f("ix_channel_connections_status"), "channel_connections", ["status"], unique=False)

    op.create_table(
        "channel_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("channel_connection_id", sa.Integer(), nullable=False),
        sa.Column("chat_log_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=120), server_default="", nullable=False),
        sa.Column("correlation_id", sa.String(length=120), server_default="", nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("external_chat_id", sa.String(length=255), server_default="", nullable=False),
        sa.Column("external_message_id", sa.String(length=255), server_default="", nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("message_type", sa.String(length=32), server_default="text", nullable=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), server_default="", nullable=False),
        sa.Column("error_message", sa.Text(), server_default="", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["channel_connection_id"], ["channel_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_log_id"], ["chat_logs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_channel_messages_org_idempotency"),
    )
    op.create_index(op.f("ix_channel_messages_channel_connection_id"), "channel_messages", ["channel_connection_id"], unique=False)
    op.create_index(op.f("ix_channel_messages_chat_log_id"), "channel_messages", ["chat_log_id"], unique=False)
    op.create_index(op.f("ix_channel_messages_conversation_id"), "channel_messages", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_channel_messages_correlation_id"), "channel_messages", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_channel_messages_direction"), "channel_messages", ["direction"], unique=False)
    op.create_index(op.f("ix_channel_messages_external_chat_id"), "channel_messages", ["external_chat_id"], unique=False)
    op.create_index(op.f("ix_channel_messages_external_message_id"), "channel_messages", ["external_message_id"], unique=False)
    op.create_index(op.f("ix_channel_messages_next_attempt_at"), "channel_messages", ["next_attempt_at"], unique=False)
    op.create_index(op.f("ix_channel_messages_organization_id"), "channel_messages", ["organization_id"], unique=False)
    op.create_index("ix_channel_messages_pending", "channel_messages", ["direction", "status", "next_attempt_at"], unique=False)
    op.create_index(op.f("ix_channel_messages_provider"), "channel_messages", ["provider"], unique=False)
    op.create_index(op.f("ix_channel_messages_status"), "channel_messages", ["status"], unique=False)
    op.create_index(op.f("ix_channel_messages_trace_id"), "channel_messages", ["trace_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_channel_messages_trace_id"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_status"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_provider"), table_name="channel_messages")
    op.drop_index("ix_channel_messages_pending", table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_organization_id"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_next_attempt_at"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_external_message_id"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_external_chat_id"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_direction"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_correlation_id"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_conversation_id"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_chat_log_id"), table_name="channel_messages")
    op.drop_index(op.f("ix_channel_messages_channel_connection_id"), table_name="channel_messages")
    op.drop_table("channel_messages")

    op.drop_index(op.f("ix_channel_connections_status"), table_name="channel_connections")
    op.drop_index(op.f("ix_channel_connections_provider"), table_name="channel_connections")
    op.drop_index(op.f("ix_channel_connections_organization_id"), table_name="channel_connections")
    op.drop_table("channel_connections")

    op.drop_index(op.f("ix_conversations_organization_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_customer_id"), table_name="conversations")
    op.drop_table("conversations")
