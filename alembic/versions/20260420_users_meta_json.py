"""users.meta_json — отказы от допродаж (cooldown) и др. пер-состояние клиента

Revision ID: 20260420_user_meta
Revises: 20260416_dedupe_states_pay_uq
Create Date: 2026-04-20

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260420_user_meta"
down_revision: Union[str, None] = "20260416_dedupe_states_pay_uq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c.get("name") for c in insp.get_columns("users")}
    except Exception:
        cols = set()
    if "meta_json" not in cols:
        op.add_column("users", sa.Column("meta_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "meta_json")
