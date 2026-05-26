"""Upsell attribution lifecycle and impact summary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.owner_intelligence_upsell import get_upsell_impact
from app.db.models import Order, Organization, UpsellOfferEvent, User
from app.services.upsell_attribution import (
    STATUS_ACCEPTED,
    STATUS_IGNORED,
    STATUS_REJECTED,
    STATUS_SHOWN,
    build_upsell_impact_summary,
    infer_upsell_acceptance_from_order,
    mark_upsell_accepted,
    mark_upsell_rejected,
    record_upsell_offer,
)
from app.services.intent_router import confirm_order


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


async def _seed_org_user(db: AsyncSession) -> tuple[int, int]:
    org = Organization(name="Upsell Org", slug="upsell-org")
    db.add(org)
    await db.flush()
    user = User(organization_id=int(org.id), phone="+77009998877", name="Guest")
    db.add(user)
    await db.flush()
    return int(org.id), int(user.id)


@pytest.mark.asyncio
async def test_upsell_lifecycle_shown_accepted_rejected_ignored(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)

    shown = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        base_item_name="Плов",
        offered_item_name="Капучино",
        offered_item_id="uuid-cappuccino",
        offered_price=1190.0,
        variant="ai_recommendation",
    )
    assert shown.status == STATUS_SHOWN

    accepted = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        base_item_name="Плов",
        offered_item_name="Чай",
        offered_item_id="uuid-tea",
        offered_price=590.0,
    )
    await mark_upsell_accepted(db_session, int(accepted.id), added_revenue=590.0)

    rejected = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        base_item_name="Лагман",
        offered_item_name="Самса",
        offered_item_id="uuid-samsa",
        offered_price=790.0,
    )
    await mark_upsell_rejected(db_session, int(rejected.id))

    order = Order(
        organization_id=org_id,
        user_id=user_id,
        status="confirmed",
        items_json={
            "items": [
                {
                    "name": "Плов",
                    "quantity": 1,
                    "iiko_item_id": "uuid-plov",
                    "item_total": 2790.0,
                },
            ],
            "order_meta": {
                "upsell_rejected_iiko_ids": ["uuid-salad"],
            },
        },
        total_price=2790.0,
    )
    db_session.add(order)
    await db_session.flush()

    inferred_offer = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order.id),
        base_item_name="Плов",
        offered_item_name="Ачичук",
        offered_item_id="uuid-achichuk",
        offered_price=490.0,
    )
    inferred_reject = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order.id),
        base_item_name="Плов",
        offered_item_name="Салат",
        offered_item_id="uuid-salad",
        offered_price=690.0,
    )
    inferred_ignore = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order.id),
        base_item_name="Плов",
        offered_item_name="Компот",
        offered_item_id="uuid-kompot",
        offered_price=390.0,
    )

    updated = await infer_upsell_acceptance_from_order(db_session, int(order.id))
    assert len(updated) == 3
    by_id = {int(ev.id): ev for ev in updated}
    assert by_id[int(inferred_offer.id)].status == STATUS_IGNORED
    assert by_id[int(inferred_reject.id)].status == STATUS_REJECTED
    assert by_id[int(inferred_ignore.id)].status == STATUS_IGNORED

    # Явное принятие с added_revenue
    acc_row = await db_session.get(UpsellOfferEvent, int(accepted.id))
    assert acc_row is not None
    assert acc_row.status == STATUS_ACCEPTED
    assert float(acc_row.added_revenue) == pytest.approx(590.0)

    rej_row = await db_session.get(UpsellOfferEvent, int(rejected.id))
    assert rej_row is not None
    assert rej_row.status == STATUS_REJECTED


@pytest.mark.asyncio
async def test_infer_upsell_acceptance_counts_revenue_from_order_line(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    order = Order(
        organization_id=org_id,
        user_id=user_id,
        status="confirmed",
        items_json={
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
        },
        total_price=3980.0,
    )
    db_session.add(order)
    await db_session.flush()

    offer = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order.id),
        base_item_name="Плов",
        offered_item_name="Капучино",
        offered_item_id="uuid-cappuccino",
        offered_price=1190.0,
    )
    updated = await infer_upsell_acceptance_from_order(db_session, int(order.id))
    assert len(updated) == 1
    assert updated[0].status == STATUS_ACCEPTED
    assert float(updated[0].added_revenue) == pytest.approx(1190.0)


@pytest.mark.asyncio
async def test_confirm_order_infers_upsell_acceptance_not_before(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    order = Order(
        organization_id=org_id,
        user_id=user_id,
        status="draft",
        items_json={
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
        },
        total_price=3980.0,
    )
    db_session.add(order)
    await db_session.flush()

    offer = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        order_id=int(order.id),
        base_item_name="Плов",
        offered_item_name="Капучино",
        offered_item_id="uuid-cappuccino",
        offered_price=1190.0,
    )
    await db_session.flush()
    assert offer.status == STATUS_SHOWN

    confirmed = await confirm_order(db_session, int(order.id), source="test")
    assert confirmed is not None
    row = await db_session.get(UpsellOfferEvent, int(offer.id))
    assert row is not None
    assert row.status == STATUS_ACCEPTED
    assert float(row.added_revenue) == pytest.approx(1190.0)


@pytest.mark.asyncio
async def test_build_upsell_impact_summary_period_and_revenue(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        base_item_name="Плов",
        offered_item_name="Чай",
        offered_item_id="uuid-tea",
        offered_price=500.0,
        variant="rule_a",
    )
    ev_old = UpsellOfferEvent(
        organization_id=org_id,
        user_id=user_id,
        base_item_name="Плов",
        offered_item_name="Старый оффер",
        offered_item_id="uuid-old",
        status=STATUS_ACCEPTED,
        offered_price=100.0,
        added_revenue=100.0,
        created_at=yesterday,
    )
    db_session.add(ev_old)

    acc = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        base_item_name="Плов",
        offered_item_name="Капучино",
        offered_item_id="uuid-cappuccino",
        offered_price=1190.0,
        variant="rule_a",
    )
    await mark_upsell_accepted(db_session, int(acc.id), added_revenue=1190.0)

    rej = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        base_item_name="Лагман",
        offered_item_name="Самса",
        offered_item_id="uuid-samsa",
        offered_price=790.0,
        variant="rule_b",
    )
    await mark_upsell_rejected(db_session, int(rej.id))
    await db_session.flush()

    summary = await build_upsell_impact_summary(db_session, org_id, "today")
    assert summary["period"] == "today"
    assert summary["shown"] == 3
    assert summary["accepted"] == 1
    assert summary["rejected"] == 1
    assert summary["conversion_rate"] == pytest.approx(33.3, abs=0.1)
    assert summary["added_revenue"] == pytest.approx(1190.0)
    assert summary["top_pairs"]
    assert any(p["offered_item_name"] == "Капучино" for p in summary["top_pairs"])
    assert summary["best_variants"]
    assert summary["rejected_items"]
    assert summary["rejected_items"][0]["offered_item_name"] == "Самса"

    week = await build_upsell_impact_summary(db_session, org_id, "week")
    assert week["shown"] == 4
    assert week["added_revenue"] == pytest.approx(1290.0)


@pytest.mark.asyncio
async def test_build_upsell_impact_summary_location_scope(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)

    await record_upsell_offer(
        db_session,
        organization_id=org_id,
        location_id=10,
        user_id=user_id,
        base_item_name="Плов",
        offered_item_name="Чай",
        offered_price=500.0,
    )
    acc = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        location_id=20,
        user_id=user_id,
        base_item_name="Плов",
        offered_item_name="Капучино",
        offered_price=1190.0,
    )
    await mark_upsell_accepted(db_session, int(acc.id), added_revenue=1190.0)
    await db_session.flush()

    loc10 = await build_upsell_impact_summary(
        db_session,
        org_id,
        "today",
        location_id=10,
    )
    assert loc10["shown"] == 1
    assert loc10["accepted"] == 0

    loc20 = await build_upsell_impact_summary(
        db_session,
        org_id,
        "today",
        location_id=20,
    )
    assert loc20["shown"] == 1
    assert loc20["accepted"] == 1
    assert loc20["added_revenue"] == pytest.approx(1190.0)


@pytest.mark.asyncio
async def test_get_upsell_impact_api_handler(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    ev = await record_upsell_offer(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        base_item_name="Плов",
        offered_item_name="Чай",
        offered_price=500.0,
    )
    await mark_upsell_accepted(db_session, int(ev.id), added_revenue=500.0)
    await db_session.flush()

    req = DummyRequest(org_id)
    data = await get_upsell_impact(req, db_session, period="today", location_id=None)
    assert data["shown"] == 1
    assert data["accepted"] == 1
    assert data["added_revenue"] == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_apply_db_upsell_rules_e2e_records_offer_with_scoring(
    db_session: AsyncSession,
) -> None:
    """Coordinator E2E: DB rule → scorer pick → UpsellOfferEvent."""
    from app.db.models import MenuItem, UpsellRule
    from app.schemas.ai_schemas import AIBrainResponse
    from app.services.strategy_engine import apply_db_upsell_rules

    org_id, user_id = await _seed_org_user(db_session)
    order = Order(
        organization_id=org_id,
        user_id=user_id,
        status="draft",
        items_json={
            "items": [{"name": "Плов", "iiko_id": "plov-1", "category": "Горячее", "quantity": 1, "price": 2500}],
            "order_meta": {},
        },
        total_price=2500.0,
    )
    db_session.add(order)
    await db_session.flush()

    tea = MenuItem(
        organization_id=org_id,
        name="Чай",
        category="Напитки",
        price=500.0,
        cost_price=50.0,
        iiko_id="tea-uuid",
        is_available=True,
    )
    cola = MenuItem(
        organization_id=org_id,
        name="Кола",
        category="Напитки",
        price=800.0,
        cost_price=200.0,
        iiko_id="cola-uuid",
        is_available=True,
    )
    db_session.add_all([tea, cola])
    rule = UpsellRule(
        organization_id=org_id,
        trigger_mode="missing_category",
        trigger_category="напит",
        suggest_category="напит",
        min_order_sum=0,
        is_active=True,
        sort_order=10,
    )
    db_session.add(rule)
    await db_session.flush()

    ai_eff = AIBrainResponse(reply_text="Ок", intent="order", items=[])
    items_json = dict(order.items_json)
    copilot_feed = {
        "promote_today_candidates": [{"iiko_id": "tea-uuid", "name": "Чай", "score": 90, "reason": "promote"}],
    }

    reply, new_ij = await apply_db_upsell_rules(
        db_session,
        organization_id=org_id,
        reply_text="Ваш заказ",
        items_json=items_json,
        grand_total=2500.0,
        menu_items=[tea, cola],
        ai_eff=ai_eff,
        order_id=int(order.id),
        user_id=user_id,
        copilot_feed=copilot_feed,
    )

    assert "💡" in reply
    events = (
        await db_session.execute(
            select(UpsellOfferEvent).where(UpsellOfferEvent.order_id == int(order.id)),
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].offered_item_id == "tea-uuid"
    assert isinstance(new_ij.get("order_meta"), dict)
    trace = new_ij["order_meta"].get("recommendation_trace") or []
    assert trace and trace[-1].get("offered_iiko_id") == "tea-uuid"
