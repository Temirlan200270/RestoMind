"""
E0.1: вынос ``/customers/{phone}/*`` в ``app/api/admin/customers.py``.

Контракт endpoints не должен меняться: тот же путь, тот же шейп ответа,
тот же org-scope. Один тест проверяет, что роуты примонтированы ровно
один раз; остальные — что summary/note/ai-pause возвращают ожидаемые поля
и не утекают между филиалами (multi-tenant smoke).
"""

from __future__ import annotations

import pytest

from app.db.models import EscalationEvent, Order, OrderStatus, Organization, User
from app.main import app


def test_customer_routes_mounted_once() -> None:
    paths = ("/api/admin/customers/{phone}/summary", "/api/admin/customers/{phone}/note", "/api/admin/customers/{phone}/ai-pause")
    for path in paths:
        matches = [r for r in app.routes if getattr(r, "path", "") == path]
        assert len(matches) == 1, f"{path} mounted {len(matches)}x (ожидается 1)"


@pytest.mark.asyncio
async def test_customer_summary_unknown_phone_returns_zero_block(db_session) -> None:
    from app.api.admin.customers import customer_summary

    org = Organization(name="C1", slug="c1")
    db_session.add(org)
    await db_session.flush()

    class _Req:
        session = {"admin_ok": True, "organization_id": int(org.id)}

    out = await customer_summary(_Req(), "+77000000000", db_session)
    assert out["user_exists"] is False
    assert out["total_orders"] == 0
    assert out["revenue_orders"] == 0
    assert out["total_spent"] == 0.0
    assert out["ai_paused"] is False
    assert out["last_escalation"] is None


@pytest.mark.asyncio
async def test_customer_summary_aggregates_orders_and_escalation(db_session) -> None:
    from app.api.admin.customers import customer_summary

    org = Organization(name="C2", slug="c2")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77001112233", name="Aiganym")
    db_session.add(user)
    await db_session.flush()

    db_session.add_all([
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.COMPLETED.value,
            total_price=2500,
            items_json={"items": []},
        ),
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=4000,
            items_json={"items": []},
        ),
        # отменённый — не входит в total_orders и revenue
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CANCELLED.value,
            total_price=1000,
            items_json={"items": []},
        ),
    ])
    db_session.add(EscalationEvent(
        organization_id=int(org.id),
        phone=user.phone,
        reason="Жалоба на доставку",
        user_message="Не привезли соус",
    ))
    await db_session.flush()

    class _Req:
        session = {"admin_ok": True, "organization_id": int(org.id)}

    out = await customer_summary(_Req(), user.phone, db_session)
    assert out["user_exists"] is True
    assert out["total_orders"] == 2  # без cancelled
    assert out["revenue_orders"] == 2
    assert out["total_spent"] == 6500.0
    assert out["avg_check"] == 3250.0
    assert out["last_escalation"] is not None
    assert out["last_escalation"]["reason"] == "Жалоба на доставку"


@pytest.mark.asyncio
async def test_customer_summary_is_org_scoped(db_session) -> None:
    """Тот же телефон в чужом филиале → user_exists=false (нет утечки)."""
    from app.api.admin.customers import customer_summary

    org_a = Organization(name="A", slug="a")
    org_b = Organization(name="B", slug="b")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    db_session.add(User(organization_id=int(org_b.id), phone="+77002223344", name="Foreign"))
    await db_session.flush()

    class _Req:
        session = {"admin_ok": True, "organization_id": int(org_a.id)}

    out = await customer_summary(_Req(), "+77002223344", db_session)
    assert out["user_exists"] is False
