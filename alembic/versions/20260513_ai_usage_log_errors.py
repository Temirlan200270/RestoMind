"""AI usage log: error_count + p95_latency_ms columns

Revision ID: 20260513_ai_usage_errors
Revises: 20260513_pipeline_latency
Create Date: 2026-05-13
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260513_ai_usage_errors"
down_revision: Union[str, None] = "20260513_pipeline_latency"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_usage_logs",
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0",
                  comment="AI-провайдер вернул transient ошибку (сумма за день)"))
    op.add_column("ai_usage_logs",
        sa.Column("p95_latency_ms", sa.Integer(), nullable=True,
                  comment="95-й перцентиль задержки LLM за день (ms)"))


def downgrade() -> None:
    op.drop_column("ai_usage_logs", "p95_latency_ms")
    op.drop_column("ai_usage_logs", "error_count")
