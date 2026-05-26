"""RC-B: DB anti-repeat, draft infer, guest preference weights."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, Organization, UpsellOfferEvent, User
from app.services.personalization import guest_preference_weights
from app.services.upsell_attribution import (
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    infer_upsell_from_draft_update,
    mark_upsell_rejected,
    order_has_upsell_offer,
    recently_offered_iiko_ids,
    recently_rejected_iiko_ids,
    record_upsell_offer,
)
from app.services.upsell_utils import max_one_upsell_per_order


async def _seed_org_user(db: AsyncSession) -> tuple[int, int]:
    org = Organization(name="Anti-repeat Org", slug="anti-repeat-org")
    db.add(org)
    await db.flush()
    user = User(organization_id=int(org.id), phone="+77001112233", name="Guest")
    db.add(user)
    await db.flush()
    return int(org.id), int(user.id)


@pytest.mark.asyncio
async def test_recently_rejected_iiko_ids_from_db_events(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    now = datetime.now(timezone.utc)

    fresh = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        offered_item_id="uuid-tea",
        offered_item_name="Чай",
        offered_price=500.0,
    )
    await mark_upsell_rejected(db_session, int(fresh.id))

    old = UpsellOfferEvent(
        organization_id=org_id,
        user_id=user_id,
        offered_item_id="uuid-old-salad",
        offered_item_name="Салат",
        status=STATUS_REJECTED,
        offered_price=600.0,
        created_at=now - timedelta(days=10),
    )
    db_session.add(old)
    await db_session.flush()

    blocked = await recently_rejected_iiko_ids(db_session, org_id, user_id, days=7)
    assert "uuid-tea" in blocked
    assert "uuid-old-salad" not in blocked


@pytest.mark.asyncio
async def test_recently_offered_iiko_ids_scoped_by_order(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)

    order_a = Order(
        organization_id=org_id,
        user_id=user_id,
        status="draft",
        items_json={"items": []},
        total_price=0.0,
    )
    order_b = Order(
        organization_id=org_id,
        user_id=user_id,
        status="draft",
        items_json={"items": []},
        total_price=0.0,
    )
    db_session.add_all([order_a, order_b])
    await db_session.flush()

    await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order_a.id),
        offered_item_id="uuid-tea",
        offered_item_name="Чай",
        offered_price=500.0,
    )
    await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order_b.id),
        offered_item_id="uuid-cappuccino",
        offered_item_name="Капучино",
        offered_price=1190.0,
    )
    await db_session.flush()

    order_a_offers = await recently_offered_iiko_ids(
        db_session,
        org_id,
        user_id,
        order_id=int(order_a.id),
    )
    assert order_a_offers == {"uuid-tea"}

    cross_order = await recently_offered_iiko_ids(db_session, org_id, user_id)
    assert cross_order == {"uuid-tea", "uuid-cappuccino"}


@pytest.mark.asyncio
async def test_max_one_upsell_per_order_guard(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    order = Order(
        organization_id=org_id,
        user_id=user_id,
        status="draft",
        items_json={"items": []},
        total_price=0.0,
    )
    db_session.add(order)
    await db_session.flush()

    assert await max_one_upsell_per_order(db_session, int(order.id)) is False
    assert await order_has_upsell_offer(db_session, int(order.id)) is False

    await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order.id),
        offered_item_id="uuid-tea",
        offered_item_name="Чай",
        offered_price=500.0,
    )
    await db_session.flush()

    assert await max_one_upsell_per_order(db_session, int(order.id)) is True
    assert await order_has_upsell_offer(db_session, int(order.id)) is True


@pytest.mark.asyncio
async def test_infer_upsell_from_draft_update_accepts_added_item(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    order = Order(
        organization_id=org_id,
        user_id=user_id,
        status="draft",
        items_json={"items": []},
        total_price=0.0,
    )
    db_session.add(order)
    await db_session.flush()

    offer = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order.id),
        offered_item_id="uuid-cappuccino",
        offered_item_name="Капучино",
        offered_price=1190.0,
    )
    await db_session.flush()

    prev = {
        "items": [
            {
                "name": "Плов",
                "quantity": 1,
                "iiko_item_id": "uuid-plov",
                "item_total": 2790.0,
            },
        ],
    }
    new = {
        "items": [
            {
                "name": "Плов",
                "quantity": 1,
                "iiko_item_id": "uuid-plov",
                "item_total": 2790.0,
            },
            {
                "name": "Капучино",
                "quantity": 1,
                "iiko_item_id": "uuid-cappuccino",
                "item_total": 1190.0,
            },
        ],
    }

    updated = await infer_upsell_from_draft_update(db_session, order, prev, new)
    assert len(updated) == 1
    assert updated[0].status == STATUS_ACCEPTED
    assert float(updated[0].added_revenue) == pytest.approx(1190.0)

    row = await db_session.get(UpsellOfferEvent, int(offer.id))
    assert row is not None
    assert row.status == STATUS_ACCEPTED


@pytest.mark.asyncio
async def test_infer_upsell_from_draft_update_rejects_on_meta(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    order = Order(
        organization_id=org_id,
        user_id=user_id,
        status="draft",
        items_json={"items": []},
        total_price=0.0,
    )
    db_session.add(order)
    await db_session.flush()

    offer = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order.id),
        offered_item_id="uuid-salad",
        offered_item_name="Салад",
        offered_price=690.0,
    )
    await db_session.flush()

    prev = {"items": [], "order_meta": {}}
    new = {
        "items": [],
        "order_meta": {"upsell_rejected_iiko_ids": ["uuid-salad"]},
    }

    updated = await infer_upsell_from_draft_update(db_session, order, prev, new)
    assert len(updated) == 1
    assert updated[0].status == STATUS_REJECTED

    row = await db_session.get(UpsellOfferEvent, int(offer.id))
    assert row is not None
    assert row.status == STATUS_REJECTED


def test_guest_preference_weights_favorite_and_never() -> None:
    prefs = {
        "favorite_categories": {"кофе", "десерты"},
        "never_categories": {"пиво"},
    }
    weights = guest_preference_weights(prefs)
    assert weights["кофе"] == pytest.approx(0.25)
    assert weights["десерты"] == pytest.approx(0.25)
    assert weights["пиво"] == pytest.approx(-0.5)


def test_guest_preference_weights_empty_without_history() -> None:
    assert guest_preference_weights({}) == {}
    assert guest_preference_weights(None) == {}
