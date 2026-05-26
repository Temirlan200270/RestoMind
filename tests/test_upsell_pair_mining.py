"""Revenue Copilot v3 — tests for upsell pair mining."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import MenuItem, Order, OrderStatus, Organization, UpsellOfferEvent, User
from app.services.upsell_pair_mining import (
    build_offer_rejection_penalties,
    build_upsell_pair_scores,
    flatten_top_mined_pairs,
    get_best_pairs_for_item,
)


@pytest.mark.asyncio
async def test_build_upsell_pair_scores_from_confirmed_orders(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Pair Org", slug="pair-org")
    user = User(organization_id=1, phone="+77001112201", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()

    db_session.add_all(
        [
            Order(
                organization_id=int(org.id),
                user_id=int(user.id),
                status=OrderStatus.CONFIRMED.value,
                total_price=5000.0,
                items_json={
                    "items": [
                        {"name": "Плов", "iiko_id": "uuid-plov", "quantity": 1, "item_total": 3000.0},
                        {"name": "Чай", "iiko_id": "uuid-tea", "quantity": 1, "item_total": 500.0},
                    ],
                },
                created_at=now,
                updated_at=now,
            ),
            Order(
                organization_id=int(org.id),
                user_id=int(user.id),
                status=OrderStatus.COMPLETED.value,
                total_price=5000.0,
                items_json={
                    "items": [
                        {"name": "Плов", "iiko_id": "uuid-plov", "quantity": 1, "item_total": 3000.0},
                        {"name": "Чай", "iiko_id": "uuid-tea", "quantity": 1, "item_total": 500.0},
                    ],
                },
                created_at=now,
                updated_at=now,
            ),
            Order(
                organization_id=int(org.id),
                user_id=int(user.id),
                status=OrderStatus.CONFIRMED.value,
                total_price=3000.0,
                items_json={
                    "items": [
                        {"name": "Плов", "iiko_id": "uuid-plov", "quantity": 1, "item_total": 3000.0},
                    ],
                },
                created_at=now,
                updated_at=now,
            ),
        ],
    )
    await db_session.flush()

    scores = await build_upsell_pair_scores(db_session, int(org.id), period="30d")
    assert "uuid-plov" in scores
    assert scores["uuid-plov"]["uuid-tea"] == 66.67


@pytest.mark.asyncio
async def test_get_best_pairs_for_item_returns_ranked_rows(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Best Pair Org", slug="best-pair-org")
    user = User(organization_id=1, phone="+77001112202", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            MenuItem(
                organization_id=int(org.id),
                name="Плов",
                category="Горячее",
                price=3000.0,
                is_available=True,
                iiko_id="uuid-plov",
            ),
            MenuItem(
                organization_id=int(org.id),
                name="Чай",
                category="Напитки",
                price=500.0,
                is_available=True,
                iiko_id="uuid-tea",
            ),
            Order(
                organization_id=int(org.id),
                user_id=int(user.id),
                status=OrderStatus.CONFIRMED.value,
                total_price=3500.0,
                items_json={
                    "items": [
                        {"name": "Плов", "iiko_id": "uuid-plov", "quantity": 1, "item_total": 3000.0},
                        {"name": "Чай", "iiko_id": "uuid-tea", "quantity": 1, "item_total": 500.0},
                    ],
                },
                created_at=now,
                updated_at=now,
            ),
        ],
    )
    await db_session.flush()

    rows = await get_best_pairs_for_item(db_session, int(org.id), "uuid-plov", limit=3)
    assert len(rows) == 1
    assert rows[0]["offered_iiko_id"] == "uuid-tea"
    assert rows[0]["offered_item_name"] == "Чай"
    assert rows[0]["score"] == 100.0


@pytest.mark.asyncio
async def test_pair_mining_tenant_isolation(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org_a = Organization(name="Org A", slug="org-a-pairs")
    org_b = Organization(name="Org B", slug="org-b-pairs")
    user_a = User(organization_id=1, phone="+77001112203", name="Guest A")
    user_b = User(organization_id=1, phone="+77001112204", name="Guest B")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    user_a.organization_id = int(org_a.id)
    user_b.organization_id = int(org_b.id)
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    db_session.add(
        Order(
            organization_id=int(org_a.id),
            user_id=int(user_a.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=3500.0,
            items_json={
                "items": [
                    {"name": "A1", "iiko_id": "a1", "quantity": 1},
                    {"name": "A2", "iiko_id": "a2", "quantity": 1},
                ],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    scores_a = await build_upsell_pair_scores(db_session, int(org_a.id), period="30d")
    scores_b = await build_upsell_pair_scores(db_session, int(org_b.id), period="30d")
    assert scores_a
    assert scores_b == {}


@pytest.mark.asyncio
async def test_build_offer_rejection_penalties(db_session) -> None:
    org = Organization(name="Penalty Org", slug="penalty-org")
    db_session.add(org)
    await db_session.flush()
    db_session.add_all(
        [
            UpsellOfferEvent(
                organization_id=int(org.id),
                offered_item_id="uuid-bad",
                offered_item_name="Bad Item",
                status="rejected",
            ),
            UpsellOfferEvent(
                organization_id=int(org.id),
                offered_item_id="uuid-bad",
                offered_item_name="Bad Item",
                status="ignored",
            ),
        ],
    )
    await db_session.flush()

    penalties = await build_offer_rejection_penalties(db_session, int(org.id), period="30d")
    assert penalties["uuid-bad"] == -16.0


@pytest.mark.asyncio
async def test_flatten_top_mined_pairs(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Flat Org", slug="flat-org")
    user = User(organization_id=1, phone="+77001112205", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=3500.0,
            items_json={
                "items": [
                    {"name": "Плов", "iiko_id": "uuid-plov", "quantity": 1},
                    {"name": "Чай", "iiko_id": "uuid-tea", "quantity": 1},
                ],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    flat = await flatten_top_mined_pairs(db_session, int(org.id), limit=5)
    assert len(flat) >= 1
    assert any(row["base_iiko_id"] == "uuid-plov" and row["offered_iiko_id"] == "uuid-tea" for row in flat)
