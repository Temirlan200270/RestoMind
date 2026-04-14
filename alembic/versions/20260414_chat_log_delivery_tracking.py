"""chat_logs: WhatsApp delivery tracking

Revision ID: 20260414_delivery
Revises: 20260401_v1
Create Date: 2026-04-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260414_delivery"
down_revision: Union[str, None] = "20260401_v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_logs",
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "chat_logs",
        sa.Column("delivery_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chat_logs",
        sa.Column("error_details", sa.JSON(), nullable=True),
    )
    op.add_column(
        "chat_logs",
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_chat_logs_provider_message_id"),
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
