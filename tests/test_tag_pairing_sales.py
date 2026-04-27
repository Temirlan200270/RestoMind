"""Strategy Engine v2: теги меню, гастро-пары, cooldown отказов в User.meta_json."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import MenuItem
from app.services.sales_strategy import build_sales_strategy
from app.services.upsell_utils import (
    parse_menu_tags,
    record_upsell_rejections_on_user,
    upsell_rejection_ids_in_cooldown,
)


def test_parse_menu_tags_splits_and_normalizes() -> None:
    t = parse_menu_tags(" spicy , main_course , drink ")
    assert "spicy" in t
    assert "main_course" in t
    assert "drink" in t


def test_build_sales_strategy_spicy_main_prioritizes_tagged_drink() -> None:
    menu = [
        MenuItem(
            name="Лагман",
            category="Первое",
            price=1990.0,
            is_available=True,
            iiko_id="uuid-lag",
            tags="spicy, main_course",
        ),
        MenuItem(
            name="Компот домашний",
            category="Напитки",
            price=390.0,
            is_available=True,
            iiko_id="uuid-comp",
            tags="drink",
        ),
        MenuItem(
            name="Салат ачичук",
            category="Салаты",
            price=890.0,
            is_available=True,
            iiko_id="uuid-ach",
            tags="side_dish",
        ),
    ]
    cart = [{"name": "Лагман", "quantity": 1, "iiko_id": "uuid-lag", "category": "Первое"}]
    d = build_sales_strategy(cart, 1990.0, {}, menu)
    assert d.goal == "upsell"
    assert d.target_iiko_ids[0] == "uuid-comp"
    gh = d.gastro_hint or ""
    assert "[Теги:" in gh
    assert "spicy" in gh and "drink" in gh


def test_build_sales_strategy_main_course_suggests_goes_well_side() -> None:
    menu = [
        MenuItem(
            name="Стейк",
            category="Горячее",
            price=4500.0,
            is_available=True,
            iiko_id="uuid-steak",
            tags="main_course",
        ),
        MenuItem(
            name="Запечённый картофель",
            category="Гарниры",
            price=590.0,
            is_available=True,
            iiko_id="uuid-potato",
            tags="goes_well_with_meat, side_dish",
        ),
        MenuItem(
            name="Кола",
            category="Напитки",
            price=400.0,
            is_available=True,
            iiko_id="uuid-cola",
            tags="drink",
        ),
    ]
    cart = [{"name": "Стейк", "quantity": 1, "iiko_id": "uuid-steak", "category": "Горячее"}]
    d = build_sales_strategy(cart, 4500.0, {}, menu)
    assert d.goal == "upsell"
    assert d.target_iiko_ids[0] == "uuid-potato"


def test_upsell_rejection_cooldown_blocks_recent() -> None:
    now = datetime.now(timezone.utc)
    meta = {"upsell_rejections": {"aaa-bbb": now.isoformat()}}
    assert "aaa-bbb" in upsell_rejection_ids_in_cooldown(meta, cooldown_hours=48.0, now=now)


def test_upsell_rejection_cooldown_expires() -> None:
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=50)).isoformat()
    meta = {"upsell_rejections": {"aaa-bbb": old}}
    assert "aaa-bbb" not in upsell_rejection_ids_in_cooldown(meta, cooldown_hours=48.0, now=now)


def test_user_cooldown_skips_achichuk_prefers_next_candidate() -> None:
    menu = [
        MenuItem(name="Плов", category="Горячее", price=2790.0, is_available=True, iiko_id="uuid-plov"),
        MenuItem(name="Капучино", category="Кофе", price=1190.0, is_available=True, iiko_id="uuid-cap"),
        MenuItem(name="Салат ачичук", category="Салаты", price=890.0, is_available=True, iiko_id="uuid-ach"),
    ]
    cart = [{"name": "Плов", "quantity": 1, "iiko_id": "uuid-plov", "category": "Горячее"}]
    now = datetime.now(timezone.utc)
    user_meta = {"upsell_rejections": {"uuid-ach": now.isoformat()}}
    d = build_sales_strategy(cart, 2790.0, {}, menu, user_meta=user_meta)
    assert d.goal == "upsell"
    assert d.target_iiko_ids[0] == "uuid-cap"


def test_record_upsell_rejections_on_user_merges() -> None:
    class U:
        meta_json = {"upsell_rejections": {"x": "2020-01-01T00:00:00+00:00"}}

    u = U()
    record_upsell_rejections_on_user(u, ["new-id", "x"])
    assert isinstance(u.meta_json, dict)
    rej = u.meta_json.get("upsell_rejections")
    assert isinstance(rej, dict)
    assert "new-id" in rej
    assert "x" in rej
