"""menu_items: tags for AI pairing / upsell (§4.2)

Revision ID: 20260404_tags
Revises: 20260401_v1
Create Date: 2026-04-04

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260404_tags"
down_revision: Union[str, None] = "20260401_v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column("tags", sa.Text(), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("menu_items", "tags")
