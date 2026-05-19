"""Add final mile modules for SupplyMind, StaffMind, Voice AI

Revision ID: 20260521_final_mile
Revises: 20260520_guestcare_inventory
Create Date: 2026-05-21 00:10:00.000000
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260521_final_mile"
down_revision: Union[str, None] = "20260520_guestcare_inventory"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "supply_purchase_drafts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
        sa.Column("source", sa.String(length=40), server_default="supplymind", nullable=False),
        sa.Column("title", sa.String(length=240), server_default="", nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supply_purchase_drafts_org_status", "supply_purchase_drafts", ["organization_id", "status"])
    op.create_index("ix_supply_purchase_drafts_org_created", "supply_purchase_drafts", ["organization_id", "created_at"])
    op.create_index(op.f("ix_supply_purchase_drafts_location_id"), "supply_purchase_drafts", ["location_id"])
    op.create_index(op.f("ix_supply_purchase_drafts_organization_id"), "supply_purchase_drafts", ["organization_id"])
    op.create_index(op.f("ix_supply_purchase_drafts_created_at"), "supply_purchase_drafts", ["created_at"])

    op.create_table(
        "staff_onboarding_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("staff_user_id", sa.Integer(), nullable=True),
        sa.Column("phone", sa.String(length=32), server_default="", nullable=False),
        sa.Column("role", sa.String(length=80), server_default="staff", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("current_step", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_json", sa.JSON(), nullable=True),
        sa.Column("last_question", sa.Text(), nullable=True),
        sa.Column("last_answer", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["staff_user_id"], ["staff_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_staff_onboarding_org_status", "staff_onboarding_sessions", ["organization_id", "status"])
    op.create_index("ix_staff_onboarding_org_phone", "staff_onboarding_sessions", ["organization_id", "phone"])
    op.create_index(op.f("ix_staff_onboarding_sessions_organization_id"), "staff_onboarding_sessions", ["organization_id"])
    op.create_index(op.f("ix_staff_onboarding_sessions_staff_user_id"), "staff_onboarding_sessions", ["staff_user_id"])
    op.create_index(op.f("ix_staff_onboarding_sessions_created_at"), "staff_onboarding_sessions", ["created_at"])

    op.create_table(
        "voice_call_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("call_sid", sa.String(length=120), server_default="", nullable=False),
        sa.Column("phone", sa.String(length=32), server_default="", nullable=False),
        sa.Column("provider", sa.String(length=40), server_default="twilio", nullable=False),
        sa.Column("mode", sa.String(length=40), server_default="stt_fallback", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="started", nullable=False),
        sa.Column("transcript", sa.Text(), server_default="", nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_voice_call_logs_org_created", "voice_call_logs", ["organization_id", "created_at"])
    op.create_index("ix_voice_call_logs_call_sid", "voice_call_logs", ["call_sid"])
    op.create_index(op.f("ix_voice_call_logs_organization_id"), "voice_call_logs", ["organization_id"])
    op.create_index(op.f("ix_voice_call_logs_created_at"), "voice_call_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_voice_call_logs_created_at"), table_name="voice_call_logs")
    op.drop_index(op.f("ix_voice_call_logs_organization_id"), table_name="voice_call_logs")
    op.drop_index("ix_voice_call_logs_call_sid", table_name="voice_call_logs")
    op.drop_index("ix_voice_call_logs_org_created", table_name="voice_call_logs")
    op.drop_table("voice_call_logs")

    op.drop_index(op.f("ix_staff_onboarding_sessions_created_at"), table_name="staff_onboarding_sessions")
    op.drop_index(op.f("ix_staff_onboarding_sessions_staff_user_id"), table_name="staff_onboarding_sessions")
    op.drop_index(op.f("ix_staff_onboarding_sessions_organization_id"), table_name="staff_onboarding_sessions")
    op.drop_index("ix_staff_onboarding_org_phone", table_name="staff_onboarding_sessions")
    op.drop_index("ix_staff_onboarding_org_status", table_name="staff_onboarding_sessions")
    op.drop_table("staff_onboarding_sessions")

    op.drop_index(op.f("ix_supply_purchase_drafts_created_at"), table_name="supply_purchase_drafts")
    op.drop_index(op.f("ix_supply_purchase_drafts_organization_id"), table_name="supply_purchase_drafts")
    op.drop_index(op.f("ix_supply_purchase_drafts_location_id"), table_name="supply_purchase_drafts")
    op.drop_index("ix_supply_purchase_drafts_org_created", table_name="supply_purchase_drafts")
    op.drop_index("ix_supply_purchase_drafts_org_status", table_name="supply_purchase_drafts")
    op.drop_table("supply_purchase_drafts")
