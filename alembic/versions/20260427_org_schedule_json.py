"""organizations schedule_json for operational status

Revision ID: 20260427_org_schedule
Revises: 20260427_superadmin
Create Date: 2026-04-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260427_org_schedule"
down_revision: Union[str, None] = "20260427_superadmin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c.get("name") for c in insp.get_columns("organizations")}
    except Exception:
        cols = set()
    if "schedule_json" not in cols:
        op.add_column("organizations", sa.Column("schedule_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    try:
        op.drop_column("organizations", "schedule_json")
    except Exception:
        pass

