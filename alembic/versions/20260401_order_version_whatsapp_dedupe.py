"""order version column + whatsapp_inbound_dedupe

Revision ID: 20260401_v1
Revises:
Create Date: 2026-04-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260401_v1"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ранняя миграция (base -> v1) может применяться к БД, где часть колонок уже
    # создана вручную/через bootstrap. Делаем шаги идемпотентными.
    bind = op.get_bind()
    insp = sa.inspect(bind)

    try:
        cols = {c.get("name") for c in insp.get_columns("orders")}
    except Exception:
        cols = set()
    if "version" not in cols:
        op.add_column(
            "orders",
            sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        )

    if not insp.has_table("whatsapp_inbound_dedupe"):
        op.create_table(
            "whatsapp_inbound_dedupe",
            sa.Column("message_id", sa.String(length=128), nullable=False),
            sa.Column("phone", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.PrimaryKeyConstraint("message_id"),
        )

    # Индекс может существовать даже если таблица уже была создана.
    try:
        idx = {i.get("name") for i in insp.get_indexes("whatsapp_inbound_dedupe")}
    except Exception:
        idx = set()
    idx_name = op.f("ix_whatsapp_inbound_dedupe_phone")
    if idx_name not in idx:
        op.create_index(
            idx_name,
            "whatsapp_inbound_dedupe",
            ["phone"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_whatsapp_inbound_dedupe_phone"), table_name="whatsapp_inbound_dedupe")
    op.drop_table("whatsapp_inbound_dedupe")
    op.drop_column("orders", "version")
