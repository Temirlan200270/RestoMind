"""RC-A / RC v3: unit-тесты upsell_scoring_engine."""

from __future__ import annotations

from app.db.models import MenuItem, UpsellOfferEvent
from app.services.upsell_scoring_engine import pick_best_candidate, rank_upsell_candidates


def _ctx(**kwargs: object) -> dict:
    base: dict = {
        "cart_items": [],
        "order_meta": {},
        "user_meta": None,
        "user_preferences": None,
        "copilot_feed": None,
        "pair_scores": None,
        "offer_frequency_penalties": None,
    }
    base.update(kwargs)
    return base


def test_margin_ranking_prefers_higher_margin() -> None:
    low = MenuItem(
        name="Салат A",
        category="Салаты",
        price=500.0,
        cost_price=400.0,
        is_available=True,
        iiko_id="uuid-low",
    )
    high = MenuItem(
        name="Салат B",
        category="Салаты",
        price=500.0,
        cost_price=100.0,
        is_available=True,
        iiko_id="uuid-high",
    )
    ranked, seen = rank_upsell_candidates([low, high], context=_ctx())
    assert seen == 2
    assert ranked[0].menu_item.iiko_id == "uuid-high"
    assert ranked[0].candidate_rank == 1
    assert any(r.startswith("margin_") for r in ranked[0].reasons)


def test_rejection_penalty_deprioritizes_rejected_item() -> None:
    rejected = MenuItem(
        name="Компот",
        category="Напитки",
        price=390.0,
        is_available=True,
        iiko_id="uuid-comp",
        tags="drink",
    )
    other = MenuItem(
        name="Чай",
        category="Напитки",
        price=350.0,
        is_available=True,
        iiko_id="uuid-tea",
        tags="drink",
    )
    cart = [{"name": "Лагман", "iiko_id": "uuid-lag", "category": "Первое"}]
    menu = [
        MenuItem(
            name="Лагман",
            category="Первое",
            price=1990.0,
            is_available=True,
            iiko_id="uuid-lag",
            tags="spicy, main_course",
        ),
        rejected,
        other,
    ]
    ev = UpsellOfferEvent(
        organization_id=1,
        offered_item_id="uuid-comp",
        status="rejected",
    )
    ranked, _ = rank_upsell_candidates(
        [rejected, other],
        context=_ctx(
            cart_items=cart,
            menu_items=menu,
            recent_rejections=[ev],
        ),
    )
    assert ranked[0].menu_item.iiko_id == "uuid-tea"
    rejected_row = next(r for r in ranked if r.menu_item.iiko_id == "uuid-comp")
    assert "penalty_rejected" in rejected_row.reasons
    assert "penalty_rejected" in rejected_row.blocked_reasons
    assert rejected_row.score < ranked[0].score


def test_promote_today_boost_wins_over_similar() -> None:
    plain = MenuItem(
        name="Кола",
        category="Напитки",
        price=400.0,
        is_available=True,
        iiko_id="uuid-cola",
        tags="drink",
    )
    promoted = MenuItem(
        name="Лимонад",
        category="Напитки",
        price=450.0,
        is_available=True,
        iiko_id="uuid-lemon",
        tags="drink",
    )
    cart = [{"name": "Стейк", "iiko_id": "uuid-steak", "category": "Горячее"}]
    menu = [
        MenuItem(
            name="Стейк",
            category="Горячее",
            price=4500.0,
            is_available=True,
            iiko_id="uuid-steak",
            tags="main_course",
        ),
        plain,
        promoted,
    ]
    feed = {
        "promote_today_candidates": [
            {"iiko_id": "uuid-lemon", "name": "Лимонад", "score": 90.0},
        ],
    }
    pick, explain = pick_best_candidate(
        [plain, promoted],
        context=_ctx(cart_items=cart, menu_items=menu, copilot_feed=feed),
    )
    assert pick is not None
    assert pick.iiko_id == "uuid-lemon"
    assert "promote_today" in explain["score_reasons"]
    assert explain["alternatives_seen"] == 2
    assert explain["candidate_rank"] == 1


def test_historical_pair_score_from_mined_context() -> None:
    tea = MenuItem(
        name="Чай",
        category="Напитки",
        price=500.0,
        is_available=True,
        iiko_id="uuid-tea",
        tags="drink",
    )
    cola = MenuItem(
        name="Кола",
        category="Напитки",
        price=600.0,
        is_available=True,
        iiko_id="uuid-cola",
        tags="drink",
    )
    cart = [{"name": "Плов", "iiko_id": "uuid-plov", "category": "Горячее", "item_total": 3000.0}]
    pair_scores = {"uuid-plov": {"uuid-tea": 80.0, "uuid-cola": 10.0}}
    ranked, _ = rank_upsell_candidates(
        [cola, tea],
        context=_ctx(cart_items=cart, pair_scores=pair_scores),
    )
    assert ranked[0].menu_item.iiko_id == "uuid-tea"
    assert any(r.startswith("historical_pair_") for r in ranked[0].reasons)


def test_high_margin_copilot_boost() -> None:
    plain = MenuItem(
        name="Вода",
        category="Напитки",
        price=300.0,
        is_available=True,
        iiko_id="uuid-water",
        tags="drink",
    )
    rich = MenuItem(
        name="Смузи",
        category="Напитки",
        price=900.0,
        cost_price=200.0,
        is_available=True,
        iiko_id="uuid-smoothie",
        tags="drink",
    )
    feed = {
        "high_margin_candidates": [
            {"iiko_id": "uuid-smoothie", "name": "Смузи", "score": 70.0},
        ],
    }
    pick, explain = pick_best_candidate(
        [plain, rich],
        context=_ctx(copilot_feed=feed),
    )
    assert pick is not None
    assert pick.iiko_id == "uuid-smoothie"
    assert "high_margin_copilot" in explain["score_reasons"]


def test_offer_frequency_penalty() -> None:
    bad = MenuItem(
        name="Компот",
        category="Напитки",
        price=400.0,
        is_available=True,
        iiko_id="uuid-bad",
        tags="drink",
    )
    good = MenuItem(
        name="Чай",
        category="Напитки",
        price=350.0,
        is_available=True,
        iiko_id="uuid-good",
        tags="drink",
    )
    ranked, _ = rank_upsell_candidates(
        [bad, good],
        context=_ctx(offer_frequency_penalties={"uuid-bad": -24.0}),
    )
    assert ranked[0].menu_item.iiko_id == "uuid-good"
    bad_row = next(r for r in ranked if r.menu_item.iiko_id == "uuid-bad")
    assert "penalty_offer_frequency" in bad_row.reasons


def test_price_vs_cart_penalty_on_low_check() -> None:
    cheap = MenuItem(
        name="Чай",
        category="Напитки",
        price=400.0,
        is_available=True,
        iiko_id="uuid-tea",
        tags="drink",
    )
    expensive = MenuItem(
        name="Стейк add-on",
        category="Горячее",
        price=500.0,
        is_available=True,
        iiko_id="uuid-steak-addon",
    )
    cart = [{"name": "Салат", "iiko_id": "uuid-salad", "item_total": 1200.0, "quantity": 1}]
    ranked, _ = rank_upsell_candidates(
        [expensive, cheap],
        context=_ctx(cart_items=cart),
    )
    assert ranked[0].menu_item.iiko_id == "uuid-tea"
    expensive_row = next(r for r in ranked if r.menu_item.iiko_id == "uuid-steak-addon")
    assert "penalty_price_low_check" in expensive_row.reasons


def test_pick_best_candidate_explainability_shape() -> None:
    item = MenuItem(
        name="Чай",
        category="Напитки",
        price=350.0,
        is_available=True,
        iiko_id="uuid-tea",
        tags="drink",
    )
    pick, explain = pick_best_candidate([item], context=_ctx())
    assert pick is not None
    assert explain["score"] is not None
    assert isinstance(explain["score_reasons"], list)
    assert isinstance(explain["blocked_reasons"], list)
    assert explain["candidate_rank"] == 1
    assert explain["alternatives_seen"] == 1
