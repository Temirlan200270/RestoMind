"""Expand orders.iiko_last_error to text.

Revision ID: 20260604_iiko_last_error_text
Revises: 20260603_user_session_version
Create Date: 2026-06-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260604_iiko_last_error_text"
down_revision = "20260603_user_session_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.alter_column(
        "orders",
        "iiko_last_error",
        existing_type=sa.String(length=512),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.alter_column(
        "orders",
        "iiko_last_error",
        existing_type=sa.Text(),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
