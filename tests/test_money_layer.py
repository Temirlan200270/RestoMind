"""Money Layer v2 — recovered metrics, queue extensions, iiko hourly ETL."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db.models import Booking, ChatLog, DailyOrgStats, Order, OrderStatus, Organization, User
from app.services.analytics_consumer import on_business_event
from app.services.iiko_sales_hourly_sync import aggregate_hourly_from_deliveries
from app.services.money_queue import build_money_queue
from app.services.money_recovery import get_recovered_today_kzt, resolve_focus_recovery_kzt
from app.services.system_events import BusinessEvent


@pytest.mark.asyncio
async def test_shift_focus_completed_increments_recovered(db_session) -> None:
    org = Organization(name="Rec Org", slug="rec-org", is_active=True)
    db_session.add(org)
    await db_session.flush()

    event = BusinessEvent(
        org_id=int(org.id),
        type="shift.focus_completed",
        actor="operator",
        entity_type="shift_focus",
        entity_id="draft:1",
        payload={"amount_kzt": 15000.0, "kind": "abandoned_draft"},
    )
    await on_business_event(event, db_session)
    await db_session.commit()

    stats = await get_recovered_today_kzt(db_session, int(org.id))
    assert stats["recovered_kzt"] == 15000.0
    assert stats["focus_completed_count"] == 1


@pytest.mark.asyncio
async def test_order_draft_recovered_increments_kzt_only(db_session) -> None:
    org = Organization(name="G6 Org", slug="g6-org", is_active=True)
    db_session.add(org)
    await db_session.flush()

    event = BusinessEvent(
        org_id=int(org.id),
        type="order.draft_recovered",
        actor="customer",
        entity_type="order",
        entity_id="42",
        payload={"amount_kzt": 9000.0},
    )
    await on_business_event(event, db_session)
    await db_session.commit()

    stats = await get_recovered_today_kzt(db_session, int(org.id))
    assert stats["recovered_kzt"] == 9000.0
    assert stats["focus_completed_count"] == 0


@pytest.mark.asyncio
async def test_resolve_focus_recovery_from_order(db_session) -> None:
    org = Organization(name="Focus Org", slug="focus-org", is_active=True)
    db_session.add(org)
    await db_session.flush()
    user = User(phone="+77001112233", organization_id=int(org.id))
    db_session.add(user)
    await db_session.flush()
    order = Order(
        user_id=int(user.id),
        organization_id=int(org.id),
        status=OrderStatus.DRAFT.value,
        total_price=12500,
        items_json={"items": [{"name": "Pizza", "qty": 1, "price": 12500}]},
    )
    db_session.add(order)
    await db_session.flush()

    amount, kind = await resolve_focus_recovery_kzt(db_session, int(org.id), f"draft:{order.id}")
    assert amount == 12500.0
    assert kind == "abandoned_draft"


@pytest.mark.asyncio
async def test_money_queue_includes_menu_confusion_and_booking(db_session) -> None:
    org = Organization(name="Queue Org", slug="queue-org", is_active=True, timezone="UTC")
    db_session.add(org)
    await db_session.flush()
    user = User(phone="+77009998877", name="Guest", organization_id=int(org.id))
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="assistant",
            content="Блюдо временно недоступно — не нашёл в меню",
        )
    )
    booking_dt = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    db_session.add(
        Booking(
            organization_id=int(org.id),
            user_id=int(user.id),
            booking_date=booking_dt.date(),
            booking_time=booking_dt.time().replace(microsecond=0),
            guests=4,
            status="pending",
        )
    )
    await db_session.flush()

    out = await build_money_queue(db_session, int(org.id))
    kinds = {it.get("kind") for it in out.get("items") or []}
    assert "menu_confusion" in kinds
    assert "booking_at_risk" in kinds
    menu_item = next(it for it in out["items"] if it["kind"] == "menu_confusion")
    assert menu_item.get("phone") == "+77009998877"


def test_aggregate_hourly_from_deliveries_fixture() -> None:
    from zoneinfo import ZoneInfo

    payload = {
        "ordersByOrganizations": [
            {
                "orders": [
                    {
                        "order": {
                            "whenCreated": "2026-05-20T14:30:00+05:00",
                            "status": "Delivered",
                            "sum": 5000,
                        }
                    }
                ]
            }
        ]
    }
    tz = ZoneInfo("Asia/Almaty")
    buckets = aggregate_hourly_from_deliveries(payload, tz=tz, default_date=date(2026, 5, 20))
    assert buckets
    assert any(v["orders_count"] == 1 for v in buckets.values())
