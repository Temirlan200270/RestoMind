"""chat_logs: WhatsApp delivery tracking

Revision ID: 20260414_delivery
Revises: 20260401_v1
Create Date: 2026-04-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260414_delivery"
down_revision: Union[str, None] = "20260404_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c.get("name") for c in insp.get_columns("chat_logs")}
    except Exception:
        cols = set()

    if "provider_message_id" not in cols:
        op.add_column(
            "chat_logs",
            sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        )
    if "delivery_status" not in cols:
        op.add_column(
            "chat_logs",
            sa.Column("delivery_status", sa.String(length=32), nullable=True),
        )
    if "error_details" not in cols:
        op.add_column(
            "chat_logs",
            sa.Column("error_details", sa.JSON(), nullable=True),
        )
    if "status_updated_at" not in cols:
        op.add_column(
            "chat_logs",
            sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    idx_name = op.f("ix_chat_logs_provider_message_id")
    try:
        idx = {i.get("name") for i in insp.get_indexes("chat_logs")}
    except Exception:
        idx = set()
    if idx_name not in idx:
        op.create_index(
            idx_name,
            "chat_logs",
            ["provider_message_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_logs_provider_message_id"), table_name="chat_logs")
    op.drop_column("chat_logs", "status_updated_at")
    op.drop_column("chat_logs", "error_details")
    op.drop_column("chat_logs", "delivery_status")
    op.drop_column("chat_logs", "provider_message_id")
