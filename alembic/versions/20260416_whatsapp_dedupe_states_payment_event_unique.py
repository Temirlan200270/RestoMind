"""whatsapp_inbound_dedupe states + payment_event idempotency unique

Revision ID: 20260416_dedupe_states_pay_uq
Revises: 20260419_menu_iiko_org
Create Date: 2026-04-16

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260416_dedupe_states_pay_uq"
down_revision: Union[str, None] = "20260419_menu_iiko_org"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # whatsapp_inbound_dedupe: добавить колонки статусов, если таблица создана старой миграцией (v1)
    if insp.has_table("whatsapp_inbound_dedupe"):
        cols = {c.get("name") for c in insp.get_columns("whatsapp_inbound_dedupe")}
        if "status" not in cols:
            op.add_column(
                "whatsapp_inbound_dedupe",
                sa.Column("status", sa.String(length=20), nullable=False, server_default="processing"),
            )
        if "attempts" not in cols:
            op.add_column(
                "whatsapp_inbound_dedupe",
                sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            )
        if "error" not in cols:
            op.add_column(
                "whatsapp_inbound_dedupe",
                sa.Column("error", sa.Text(), nullable=False, server_default=""),
            )
        if "claimed_at" not in cols:
            op.add_column(
                "whatsapp_inbound_dedupe",
                sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            )
        if "processed_at" not in cols:
            op.add_column(
                "whatsapp_inbound_dedupe",
                sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            )

    # payment_events: уникальность идемпотентности (order_id, event_type, note)
    if insp.has_table("payment_events"):
        uqs = {u.get("name") for u in insp.get_unique_constraints("payment_events")}
        if "uq_payment_event_idempotency" not in uqs:
            op.create_unique_constraint(
                "uq_payment_event_idempotency",
                "payment_events",
                ["order_id", "event_type", "note"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("payment_events"):
        uqs = {u.get("name") for u in insp.get_unique_constraints("payment_events")}
        if "uq_payment_event_idempotency" in uqs:
            op.drop_constraint("uq_payment_event_idempotency", "payment_events", type_="unique")

    if insp.has_table("whatsapp_inbound_dedupe"):
        cols = {c.get("name") for c in insp.get_columns("whatsapp_inbound_dedupe")}
        # drop in reverse-ish order
        if "processed_at" in cols:
            op.drop_column("whatsapp_inbound_dedupe", "processed_at")
        if "claimed_at" in cols:
            op.drop_column("whatsapp_inbound_dedupe", "claimed_at")
        if "error" in cols:
            op.drop_column("whatsapp_inbound_dedupe", "error")
        if "attempts" in cols:
            op.drop_column("whatsapp_inbound_dedupe", "attempts")
        if "status" in cols:
            op.drop_column("whatsapp_inbound_dedupe", "status")

