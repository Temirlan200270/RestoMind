"""multitenant: org fields, staff_users, user org+phone unique, chat_logs org, recommendation_events, knowledge_kind

Revision ID: 20260415_mt
Revises: 20260414_delivery
Create Date: 2026-04-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260415_mt"
down_revision: Union[str, None] = "20260414_delivery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.add_column(
        "organizations",
        sa.Column("slug", sa.String(length=120), server_default="", nullable=False),
    )
    op.add_column(
        "organizations",
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
    )
    op.add_column(
        "organizations",
        sa.Column("currency", sa.String(length=8), server_default="KZT", nullable=False),
    )
    op.add_column(
        "organizations",
        sa.Column("telegram_ops_chat_id", sa.String(length=32), server_default="", nullable=False),
    )
    op.create_index(op.f("ix_organizations_slug"), "organizations", ["slug"], unique=False)

    op.create_table(
        "staff_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=32), server_default="admin", nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_staff_users_organization_id"), "staff_users", ["organization_id"], unique=False)
    op.create_index(op.f("ix_staff_users_email"), "staff_users", ["email"], unique=True)

    op.add_column(
        "knowledge_items",
        sa.Column(
            "knowledge_kind",
            sa.String(length=32),
            server_default="facility",
            nullable=False,
        ),
    )

    op.add_column(
        "integration_events",
        sa.Column("organization_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_integration_events_organization_id",
        "integration_events",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_integration_events_organization_id"),
        "integration_events",
        ["organization_id"],
        unique=False,
    )

    op.add_column(
        "escalation_events",
        sa.Column("organization_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_escalation_events_organization_id",
        "escalation_events",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_escalation_events_organization_id"),
        "escalation_events",
        ["organization_id"],
        unique=False,
    )

    op.add_column(
        "failed_tasks",
        sa.Column("organization_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_failed_tasks_organization_id",
        "failed_tasks",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_failed_tasks_organization_id"),
        "failed_tasks",
        ["organization_id"],
        unique=False,
    )

    # Дефолтная организация, если таблица пуста
    op.execute(
        sa.text(
            "INSERT INTO organizations (name, slug, timezone, currency, iiko_api_login, "
            "iiko_organization_id, whatsapp_phone_number_id, telegram_ops_chat_id, is_active) "
            "SELECT 'Default', 'default', 'UTC', 'KZT', '', '', '', '', 1 "
            "WHERE NOT EXISTS (SELECT 1 FROM organizations LIMIT 1)",
        ),
    )

    # users: заполнить organization_id и сменить уникальность (phone → org+phone)
    op.execute(
        sa.text(
            "UPDATE users SET organization_id = (SELECT id FROM organizations ORDER BY id ASC LIMIT 1) "
            "WHERE organization_id IS NULL",
        ),
    )
    op.execute(
        sa.text(
            "UPDATE orders SET organization_id = (SELECT organization_id FROM users WHERE users.id = orders.user_id) "
            "WHERE organization_id IS NULL",
        ),
    )
    op.execute(
        sa.text(
            "UPDATE bookings SET organization_id = (SELECT organization_id FROM users WHERE users.id = bookings.user_id) "
            "WHERE organization_id IS NULL",
        ),
    )

    op.add_column(
        "chat_logs",
        sa.Column("organization_id", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE chat_logs SET organization_id = (SELECT organization_id FROM users WHERE users.id = chat_logs.user_id) "
            "WHERE organization_id IS NULL",
        ),
    )

    op.alter_column(
        "chat_logs",
        "organization_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_chat_logs_organization_id",
        "chat_logs",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_chat_logs_organization_id"),
        "chat_logs",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "recommendation_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("item_iiko_id", sa.String(length=100), nullable=True),
        sa.Column("item_name", sa.String(length=255), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_recommendation_events_organization_id"),
        "recommendation_events",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recommendation_events_order_id"),
        "recommendation_events",
        ["order_id"],
        unique=False,
    )

    if is_sqlite:
        with op.batch_alter_table("users", schema=None, recreate="always") as batch_op:
            batch_op.alter_column(
                "organization_id",
                existing_type=sa.Integer(),
                nullable=False,
            )
            batch_op.create_unique_constraint("uq_users_organization_phone", ["organization_id", "phone"])
    else:
        op.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_phone_key"))
        op.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ix_users_phone"))
        op.alter_column(
            "users",
            "organization_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        op.create_unique_constraint("uq_users_organization_phone", "users", ["organization_id", "phone"])


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        with op.batch_alter_table("users", schema=None, recreate="always") as batch_op:
            batch_op.drop_constraint("uq_users_organization_phone", type_="unique")
            batch_op.create_unique_constraint(op.f("users_phone_key"), ["phone"])
            batch_op.alter_column("organization_id", existing_type=sa.Integer(), nullable=True)
    else:
        op.drop_constraint("uq_users_organization_phone", "users", type_="unique")
        op.create_unique_constraint(op.f("users_phone_key"), "users", ["phone"])
        op.alter_column("users", "organization_id", existing_type=sa.Integer(), nullable=True)

    op.drop_index(op.f("ix_recommendation_events_order_id"), table_name="recommendation_events")
    op.drop_index(op.f("ix_recommendation_events_organization_id"), table_name="recommendation_events")
    op.drop_table("recommendation_events")

    op.drop_index(op.f("ix_chat_logs_organization_id"), table_name="chat_logs")
    op.drop_constraint("fk_chat_logs_organization_id", "chat_logs", type_="foreignkey")
    op.drop_column("chat_logs", "organization_id")

    op.drop_index(op.f("ix_failed_tasks_organization_id"), table_name="failed_tasks")
    op.drop_constraint("fk_failed_tasks_organization_id", "failed_tasks", type_="foreignkey")
    op.drop_column("failed_tasks", "organization_id")

    op.drop_index(op.f("ix_escalation_events_organization_id"), table_name="escalation_events")
    op.drop_constraint("fk_escalation_events_organization_id", "escalation_events", type_="foreignkey")
    op.drop_column("escalation_events", "organization_id")

    op.drop_index(op.f("ix_integration_events_organization_id"), table_name="integration_events")
    op.drop_constraint("fk_integration_events_organization_id", "integration_events", type_="foreignkey")
    op.drop_column("integration_events", "organization_id")

    op.drop_column("knowledge_items", "knowledge_kind")

    op.drop_index(op.f("ix_staff_users_email"), table_name="staff_users")
    op.drop_index(op.f("ix_staff_users_organization_id"), table_name="staff_users")
    op.drop_table("staff_users")

    op.drop_index(op.f("ix_organizations_slug"), table_name="organizations")
    op.drop_column("organizations", "telegram_ops_chat_id")
    op.drop_column("organizations", "currency")
    op.drop_column("organizations", "timezone")
    op.drop_column("organizations", "slug")
