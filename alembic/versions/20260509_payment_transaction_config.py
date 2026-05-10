"""per-org payment provider config JSON column

Добавляет Organization.payment_config_json — хранит конфигурацию платёжных провайдеров
(Freedom Pay, Kaspi Pay, CloudPayments) на уровне заведения. Секреты провайдеров
предполагается хранить зашифрованными через Fernet (APP_SECRETS_FERNET_KEY).

Структура JSON:
{
  "freedom_pay":   {"enabled": false, "webhook_secret_enc": null},
  "kaspi":         {"enabled": false, "webhook_secret_enc": null},
  "cloudpayments": {"enabled": false, "api_secret_enc": null}
}

Revision ID: 20260509_payment_cfg
Revises: 20260508_intelligence_ops
Create Date: 2026-05-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260509_payment_cfg"
down_revision: Union[str, None] = "20260508_intelligence_ops"
branch_labels: Sequence[str] | None = None
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
