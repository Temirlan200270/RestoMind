"""Restaurant intelligence and digital twin foundation

Revision ID: 20260508_intelligence_ops
Revises: 20260507_force_close_tokens
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260508_intelligence_ops"
down_revision: Union[str, None] = "20260507_force_close_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("source", sa.String(80), nullable=False, server_default="app"),
        sa.Column("entity_type", sa.String(80), nullable=True),
        sa.Column("entity_id", sa.String(120), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_system_event_idempotency_key"),
    )
    op.create_index("ix_system_events_organization_id", "system_events", ["organization_id"])
    op.create_index("ix_system_events_event_type", "system_events", ["event_type"])
    op.create_index("ix_system_events_entity_id", "system_events", ["entity_id"])
    op.create_index("ix_system_events_created_at", "system_events", ["created_at"])
    op.create_index("ix_system_events_org_created", "system_events", ["organization_id", "created_at"])
    op.create_index("ix_system_events_org_type_created", "system_events", ["organization_id", "event_type", "created_at"])

    op.create_table(
        "operational_insights",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("insight_type", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_operational_insights_organization_id", "operational_insights", ["organization_id"])
    op.create_index("ix_operational_insights_insight_type", "operational_insights", ["insight_type"])
    op.create_index("ix_operational_insights_status", "operational_insights", ["status"])
    op.create_index("ix_operational_insights_created_at", "operational_insights", ["created_at"])
    op.create_index(
        "ix_operational_insights_org_status_created",
        "operational_insights",
        ["organization_id", "status", "created_at"],
    )

    op.create_table(
        "restaurant_state_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("active_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draft_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancelled_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue_today", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("avg_check_today", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("queue_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operator_load", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("kitchen_load", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("stoplist_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_restaurant_state_snapshots_organization_id", "restaurant_state_snapshots", ["organization_id"])
    op.create_index("ix_restaurant_state_snapshots_created_at", "restaurant_state_snapshots", ["created_at"])
    op.create_index(
        "ix_restaurant_state_snapshots_org_created",
        "restaurant_state_snapshots",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "intelligence_conversations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("title", sa.String(240), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_intelligence_conversations_organization_id", "intelligence_conversations", ["organization_id"])
    op.create_index("ix_intelligence_conversations_created_at", "intelligence_conversations", ["created_at"])

    op.create_table(
        "intelligence_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("intelligence_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_intelligence_messages_conversation_id", "intelligence_messages", ["conversation_id"])
    op.create_index("ix_intelligence_messages_organization_id", "intelligence_messages", ["organization_id"])
    op.create_index("ix_intelligence_messages_created_at", "intelligence_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_intelligence_messages_created_at", table_name="intelligence_messages")
    op.drop_index("ix_intelligence_messages_organization_id", table_name="intelligence_messages")
    op.drop_index("ix_intelligence_messages_conversation_id", table_name="intelligence_messages")
    op.drop_table("intelligence_messages")
    op.drop_index("ix_intelligence_conversations_created_at", table_name="intelligence_conversations")
    op.drop_index("ix_intelligence_conversations_organization_id", table_name="intelligence_conversations")
    op.drop_table("intelligence_conversations")
    op.drop_index("ix_restaurant_state_snapshots_org_created", table_name="restaurant_state_snapshots")
    op.drop_index("ix_restaurant_state_snapshots_created_at", table_name="restaurant_state_snapshots")
    op.drop_index("ix_restaurant_state_snapshots_organization_id", table_name="restaurant_state_snapshots")
    op.drop_table("restaurant_state_snapshots")
    op.drop_index("ix_operational_insights_org_status_created", table_name="operational_insights")
    op.drop_index("ix_operational_insights_created_at", table_name="operational_insights")
    op.drop_index("ix_operational_insights_status", table_name="operational_insights")
    op.drop_index("ix_operational_insights_insight_type", table_name="operational_insights")
    op.drop_index("ix_operational_insights_organization_id", table_name="operational_insights")
    op.drop_table("operational_insights")
    op.drop_index("ix_system_events_org_type_created", table_name="system_events")
    op.drop_index("ix_system_events_org_created", table_name="system_events")
    op.drop_index("ix_system_events_created_at", table_name="system_events")
    op.drop_index("ix_system_events_entity_id", table_name="system_events")
    op.drop_index("ix_system_events_event_type", table_name="system_events")
    op.drop_index("ix_system_events_organization_id", table_name="system_events")
    op.drop_table("system_events")
