"""menu_items: unique per (organization_id, iiko_id)

Revision ID: 20260419_menu_iiko_org
Revises: 20260418_pay
Create Date: 2026-04-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260419_menu_iiko_org"
down_revision: Union[str, None] = "20260418_pay"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Ранее iiko_id был unique=True → создавался глобальный уникальный индекс
    ix_menu_items_iiko_id. В multi-tenant это неверно: одинаковый UUID iiko может
    встречаться у разных организаций.

    Исправляем на уникальность (organization_id, iiko_id).
    """
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # Drop старого уникального индекса/constraint (best effort)
    if is_sqlite:
        # В SQLite проще пересоздать таблицу батчем с нужным constraint.
        with op.batch_alter_table("menu_items", schema=None, recreate="always") as batch_op:
            batch_op.create_unique_constraint(
                "uq_menu_items_org_iiko_id",
                ["organization_id", "iiko_id"],
            )
    else:
        # Postgres: индекс мог существовать как UNIQUE INDEX с именем ix_menu_items_iiko_id.
        op.execute(sa.text("DROP INDEX IF EXISTS ix_menu_items_iiko_id"))
        op.execute(sa.text("ALTER TABLE menu_items DROP CONSTRAINT IF EXISTS menu_items_iiko_id_key"))
        op.create_unique_constraint(
            "uq_menu_items_org_iiko_id",
            "menu_items",
            ["organization_id", "iiko_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        with op.batch_alter_table("menu_items", schema=None, recreate="always") as batch_op:
            batch_op.drop_constraint("uq_menu_items_org_iiko_id", type_="unique")
            # Возвращаем глобальную уникальность (как было)
            batch_op.create_unique_constraint("menu_items_iiko_id_key", ["iiko_id"])
    else:
        op.drop_constraint("uq_menu_items_org_iiko_id", "menu_items", type_="unique")
        op.create_index("ix_menu_items_iiko_id", "menu_items", ["iiko_id"], unique=True)

