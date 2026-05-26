"""Tests for Menu Profit Lab copilot candidate feed (RC-D)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import MenuItem, Order, OrderStatus, Organization, SystemEvent, User
from app.services.menu_profit_lab import get_copilot_candidate_lists


def _feed_item_keys(item: dict) -> set[str]:
    return set(item.keys())


@pytest.mark.asyncio
async def test_copilot_feed_returns_four_lists(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Copilot Org", slug="copilot-org")
    user = User(organization_id=1, phone="+77006660001", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        MenuItem(
            organization_id=int(org.id),
            name="Плов",
            category="Горячее",
            price=3000.0,
            is_available=True,
            iiko_id="uuid-plov",
        ),
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=6000.0,
            items_json={
                "items": [
                    {"name": "Плов", "quantity": 2, "item_total": 6000.0, "iiko_id": "uuid-plov"},
                ],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    feed = await get_copilot_candidate_lists(db_session, int(org.id), period="7d")

    assert set(feed.keys()) == {
        "promote_today_candidates",
        "high_margin_candidates",
        "overstock_candidates",
        "low_performing_but_profitable",
    }
    assert all(isinstance(feed[key], list) for key in feed)
    assert len(feed["promote_today_candidates"]) >= 1
    assert feed["promote_today_candidates"][0]["name"] == "Плов"
    assert _feed_item_keys(feed["promote_today_candidates"][0]) == {"iiko_id", "name", "score", "reason"}


@pytest.mark.asyncio
async def test_copilot_feed_high_margin_and_low_performing(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Margin Feed Org", slug="margin-feed-org")
    user = User(organization_id=1, phone="+77006660002", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            MenuItem(
                organization_id=int(org.id),
                name="Стейк",
                category="Горячее",
                price=10000.0,
                cost_price=3000.0,
                is_available=True,
                iiko_id="uuid-steak",
            ),
            MenuItem(
                organization_id=int(org.id),
                name="Десерт",
                category="Сладкое",
                price=5000.0,
                cost_price=1000.0,
                is_available=True,
                iiko_id="uuid-dessert",
            ),
        ],
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=15000.0,
            items_json={
                "items": [
                    {"name": "Стейк", "quantity": 1, "item_total": 10000.0, "iiko_id": "uuid-steak"},
                    {"name": "Десерт", "quantity": 1, "item_total": 5000.0, "iiko_id": "uuid-dessert"},
                ],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    feed = await get_copilot_candidate_lists(db_session, int(org.id), period="7d")

    high_margin_names = {item["name"] for item in feed["high_margin_candidates"]}
    assert "Стейк" in high_margin_names
    assert "Десерт" in high_margin_names
    assert all(item["reason"] == "high_margin" for item in feed["high_margin_candidates"])

    low_perf_names = {item["name"] for item in feed["low_performing_but_profitable"]}
    assert low_perf_names.issubset({"Стейк", "Десерт"})
    assert all(item["reason"] == "low_volume_high_margin" for item in feed["low_performing_but_profitable"])


@pytest.mark.asyncio
async def test_copilot_feed_overstock_from_stoplist(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Overstock Org", slug="overstock-org")
    db_session.add(org)
    await db_session.flush()
    db_session.add(
        MenuItem(
            organization_id=int(org.id),
            name="Самса",
            category="Выпечка",
            price=800.0,
            is_available=False,
            iiko_id="uuid-samsa",
        ),
    )
    db_session.add_all(
        [
            SystemEvent(
                organization_id=int(org.id),
                event_type="stoplist_update",
                payload_json={"items_added_to_stop": ["Самса"]},
                created_at=now,
            ),
            SystemEvent(
                organization_id=int(org.id),
                event_type="stoplist_update",
                payload_json={"items_added_to_stop": ["Самса"]},
                created_at=now - timedelta(hours=1),
            ),
        ],
    )
    await db_session.flush()

    feed = await get_copilot_candidate_lists(db_session, int(org.id), period="7d")

    assert len(feed["overstock_candidates"]) >= 1
    assert feed["overstock_candidates"][0]["name"] == "Самса"
    assert feed["overstock_candidates"][0]["iiko_id"] == "uuid-samsa"
    assert feed["overstock_candidates"][0]["reason"] == "overstock_stoplist"


@pytest.mark.asyncio
async def test_copilot_feed_tenant_isolation(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org_a = Organization(name="Feed Org A", slug="feed-org-a")
    org_b = Organization(name="Feed Org B", slug="feed-org-b")
    user_a = User(organization_id=1, phone="+77007770001", name="A")
    user_b = User(organization_id=1, phone="+77007770002", name="B")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    user_a.organization_id = int(org_a.id)
    user_b.organization_id = int(org_b.id)
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    db_session.add_all(
        [
            MenuItem(organization_id=int(org_a.id), name="Блюдо A", price=1000.0, iiko_id="a1"),
            MenuItem(organization_id=int(org_b.id), name="Блюдо B", price=2000.0, iiko_id="b1"),
        ],
    )
    db_session.add(
        Order(
            organization_id=int(org_a.id),
            user_id=int(user_a.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=1000.0,
            items_json={"items": [{"name": "Блюдо A", "quantity": 1, "item_total": 1000.0, "iiko_id": "a1"}]},
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    feed_a = await get_copilot_candidate_lists(db_session, int(org_a.id), period="7d")
    feed_b = await get_copilot_candidate_lists(db_session, int(org_b.id), period="7d")

    assert feed_a["promote_today_candidates"][0]["name"] == "Блюдо A"
    assert feed_b["promote_today_candidates"] == []
