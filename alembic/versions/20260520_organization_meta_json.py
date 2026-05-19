"""Add organizations.meta_json for lightweight per-org extensions

Revision ID: 20260520_org_meta_json
Revises: 20260520_locations_phase11
Create Date: 2026-05-20
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260520_org_meta_json"
down_revision: Union[str, None] = "20260520_locations_phase11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("meta_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "meta_json")
