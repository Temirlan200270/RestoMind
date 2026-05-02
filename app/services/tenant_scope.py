"""
Общие SQLAlchemy-условия для мультитенантности и legacy-строк без organization_id.

Централизуем, чтобы админка, ROI и фоновые сервисы (авто-iiko и т.д.) не расходились.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select

from app.db.models import FailedTask, Order, User


def phones_subquery_for_org(org_id: int):
    return select(User.phone).where(User.organization_id == org_id)


def orders_tenant_clause(org_id: int):
    """Заказы филиала: явный organization_id или legacy через user."""
    return or_(
        Order.organization_id == org_id,
        and_(
            Order.organization_id.is_(None),
            Order.user_id.in_(select(User.id).where(User.organization_id == org_id)),
        ),
    )


def failed_tasks_tenant_clause(org_id: int):
    return or_(
        FailedTask.organization_id == org_id,
        and_(
            FailedTask.organization_id.is_(None),
            FailedTask.phone.in_(phones_subquery_for_org(org_id)),
        ),
    )
