"""E4.2: атомарный optimistic UPDATE черновика — без «полусохранённых» строк."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, Organization, User
from app.services.order_logic import persist_draft_order_optimistic_update


@pytest.mark.asyncio
async def test_persist_draft_order_optimistic_update_success(db_session: AsyncSession) -> None:
    db_session.add(Organization(id=1, name="O", slug="o"))
    await db_session.flush()
    u = User(phone="+77001112233", organization_id=1)
    db_session.add(u)
    await db_session.flush()
    order = Order(
        organization_id=1,
        user_id=u.id,
        status=OrderStatus.DRAFT.value,
        items_json={"items": []},
        total_price=100.0,
        prepayment_status="not_required",
        row_version=3,
    )
    db_session.add(order)
    await db_session.flush()

    ok = await persist_draft_order_optimistic_update(
        db_session,
        order_id=order.id,
        expected_row_version=3,
        items_json={"items": [{"name": "X", "quantity": 1}]},
        total_price=200.0,
        prepayment_status="not_required",
        booking_id=None,
        organization_id=1,
    )
    assert ok is True
    await db_session.refresh(order)
    assert int(order.row_version) == 4
    assert float(order.total_price) == 200.0


@pytest.mark.asyncio
async def test_persist_draft_order_optimistic_update_no_op_when_version_stale(
    db_session: AsyncSession,
) -> None:
    db_session.add(Organization(id=1, name="O", slug="o"))
    await db_session.flush()
    u = User(phone="+77001112244", organization_id=1)
    db_session.add(u)
    await db_session.flush()
    order = Order(
        organization_id=1,
        user_id=u.id,
        status=OrderStatus.DRAFT.value,
        items_json={"items": [{"name": "Old", "quantity": 1}]},
        total_price=500.0,
        prepayment_status="not_required",
        row_version=7,
    )
    db_session.add(order)
    await db_session.flush()
    oid = order.id

    ok = await persist_draft_order_optimistic_update(
        db_session,
        order_id=oid,
        expected_row_version=6,
        items_json={"items": [{"name": "New", "quantity": 99}]},
        total_price=999.0,
        prepayment_status="not_required",
        booking_id=None,
        organization_id=1,
    )
    assert ok is False
    await db_session.refresh(order)
    assert int(order.row_version) == 7
    assert float(order.total_price) == 500.0
    items = (order.items_json or {}).get("items") if isinstance(order.items_json, dict) else []
    assert isinstance(items, list) and items and items[0].get("name") == "Old"
