from types import SimpleNamespace

from app.services.strategy_engine import _menu_candidates
from app.services.upsell_utils import is_forbidden_upsell_candidate


def test_milk_forbidden_for_hot_meat_context() -> None:
    milk_item = SimpleNamespace(
        name="Молоко фермерское",
        category="Напитки",
        tags="milk",
        is_available=True,
        organization_id=1,
        iiko_id="milk-1",
        price=200,
    )
    assert is_forbidden_upsell_candidate(milk_item, cart_tags={"main_course", "meat"})


def test_menu_candidates_preferred_before_regular_by_price() -> None:
    regular_cheap = SimpleNamespace(
        name="Вода негазированная",
        category="Напитки",
        tags="",
        is_available=True,
        organization_id=1,
        iiko_id="water-1",
        price=100,
    )
    preferred_expensive = SimpleNamespace(
        name="Чай зеленый",
        category="Напитки",
        tags="drink,upsell",
        is_available=True,
        organization_id=1,
        iiko_id="tea-1",
        price=300,
    )
    out = _menu_candidates(
        [regular_cheap, preferred_expensive],
        organization_id=1,
        suggest_substr="",
        exclude_iiko=set(),
        cart_tags={"main_course"},
    )
    assert out[0].name == "Чай зеленый"
