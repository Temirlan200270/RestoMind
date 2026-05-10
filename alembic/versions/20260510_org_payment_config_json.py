"""Add payment_config_json column to organizations

Колонка пропущена в 20260509_payment_tx_config (миграция была уже применена
до добавления этого поля в модель). Добавляем отдельной догоняющей миграцией.

Revision ID: 20260510_org_pay_cfg_json
Revises: 20260509_payment_tx_config
Create Date: 2026-05-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260510_org_pay_cfg_json"
down_revision: Union[str, None] = "20260509_payment_tx_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "payment_config_json",
            sa.JSON(),
            nullable=True,
            comment=(
                "Per-org конфигурация платёжных провайдеров: "
                "{provider: {enabled, webhook_secret_enc, extra_json}}. "
                "Секреты хранятся Fernet-зашифрованными."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "payment_config_json")
