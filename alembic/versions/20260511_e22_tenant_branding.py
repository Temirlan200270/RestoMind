"""E2.2 — Tenant.brand_* fields for admin branding.

Добавляет в `tenants` поля бренда (название/цвет/URL логотипа), которые потом
читает GET/PATCH /api/admin/branding и контракт GET /api/admin/auth/me → branding.
До этой миграции `branding` в /auth/me возвращался плейсхолдером со всеми null.

Revision ID: 20260511_e22_branding
Revises: 20260510_org_pay_cfg_json
Create Date: 2026-05-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260511_e22_branding"
down_revision: Union[str, None] = "20260510_org_pay_cfg_json"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "brand_name",
            sa.String(length=255),
            nullable=True,
            comment="Кастомное название бренда в шапке админки",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "brand_color_hex",
            sa.String(length=9),
            nullable=True,
            comment="HEX цвета акцента (#RRGGBB), валидируется на бэкенде",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "brand_logo_url",
            sa.String(length=512),
            nullable=True,
            comment="Публичный URL логотипа (заполняется загрузчиком POST /branding/logo)",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "brand_logo_url")
    op.drop_column("tenants", "brand_color_hex")
    op.drop_column("tenants", "brand_name")
