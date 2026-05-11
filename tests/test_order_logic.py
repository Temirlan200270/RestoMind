"""
Тесты валидации заказов (validate_order, build_menu_context).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem
from app.schemas.ai_schemas import AIBrainResponse, OrderAction, OrderItem
from app.services.order_logic import (
    build_menu_context,
    build_summary_text_from_stored_items,
    calculate_total_and_fees,
    compute_order_action_stable_id,
    detect_payment_method_from_text,
    draft_food_lines_to_order_items,
    enrich_merged_items_from_menu,
    filter_order_actions_idempotent,
    format_draft_order_context_for_prompt,
    format_whatsapp_order_card,
    load_available_menu,
    merge_applied_order_action_ids_into_items_json,
    merge_cart_actions,
    validate_order,
)


@pytest.mark.asyncio
async def test_load_available_menu_requires_organization_id(db_with_menu: AsyncSession) -> None:
    """Вызов без organization_id — ошибка (нет «глобального» меню)."""
    with pytest.raises(TypeError, match="organization_id"):
        await load_available_menu(db_with_menu)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_validate_order_requires_org_when_loading_from_db(db_with_menu: AsyncSession) -> None:
    """validate_order с db и без menu_items требует organization_id."""
    items = [OrderItem(name="Плов", quantity=1)]
    with pytest.raises(ValueError, match="organization_id"):
        await validate_order(items, db=db_with_menu)


@pytest.mark.asyncio
async def test_validate_known_items(db_with_menu: AsyncSession) -> None:
    """Все позиции есть в меню — заказ валиден."""
    items = [
        OrderItem(name="Плов", quantity=2),
        OrderItem(name="Капучино", quantity=1),
    ]
    menu = await load_available_menu(db_with_menu, organization_id=1)
    result = await validate_order(items, menu_items=menu)

    assert len(result.valid_items) == 2
    assert len(result.unknown_items) == 0
    assert result.total_price == 2790.0 * 2 + 1190.0


@pytest.mark.asyncio
async def test_validate_unknown_items(db_with_menu: AsyncSession) -> None:
    """Неизвестная позиция попадает в unknown_items."""
    items = [
        OrderItem(name="Плов", quantity=1),
        OrderItem(name="Единорог на гриле", quantity=1),
    ]
    menu = await load_available_menu(db_with_menu, organization_id=1)
    result = await validate_order(items, menu_items=menu)

    assert len(result.valid_items) == 1
    assert "Единорог на гриле" in result.unknown_items


@pytest.mark.asyncio
async def test_validate_empty_items(db_with_menu: AsyncSession) -> None:
    """Пустой список — нет ошибки, нет позиций."""
    menu = await load_available_menu(db_with_menu, organization_id=1)
    result = await validate_order([], menu_items=menu)

    assert result.valid_items == []
    assert result.total_price == 0.0


@pytest.mark.asyncio
async def test_validate_fuzzy_match(db_with_menu: AsyncSession) -> None:
    """Fuzzy matching: 'капучинно' → 'Капучино'."""
    items = [OrderItem(name="капучинно", quantity=1)]
    menu = await load_available_menu(db_with_menu, organization_id=1)
    result = await validate_order(items, menu_items=menu)

    assert len(result.valid_items) == 1
    assert result.total_price == 1190.0
    assert len(result.fuzzy_match_details) == 1
    assert result.fuzzy_match_details[0]["source_name"] == "капучинно"


@pytest.mark.asyncio
async def test_validate_iiko_id_preserved(db_with_menu: AsyncSession) -> None:
    """iiko_id из меню сохраняется в validated items."""
    items = [OrderItem(name="Лагман", quantity=1)]
    menu = await load_available_menu(db_with_menu, organization_id=1)
    result = await validate_order(items, menu_items=menu)

    assert result.valid_items[0]["iiko_id"] == "uuid-lagman"


@pytest.mark.asyncio
async def test_validate_exclude_ingredients(db_with_menu: AsyncSession) -> None:
    """exclude_ingredients сохраняются в валидированном заказе."""
    items = [OrderItem(name="Плов", quantity=1, exclude_ingredients=["лук", "морковь"])]
    menu = await load_available_menu(db_with_menu, organization_id=1)
    result = await validate_order(items, menu_items=menu)

    assert result.valid_items[0]["exclude_ingredients"] == ["лук", "морковь"]
    assert "без лук" in result.summary_text


@pytest.mark.asyncio
async def test_validate_all_unknown_returns_empty(db_with_menu: AsyncSession) -> None:
    """Все позиции неизвестны → valid_items пуст."""
    items = [
        OrderItem(name="Жареный вулкан", quantity=1),
        OrderItem(name="Суп из радуги", quantity=2),
    ]
    menu = await load_available_menu(db_with_menu, organization_id=1)
    result = await validate_order(items, menu_items=menu)

    assert result.valid_items == []
    assert len(result.unknown_items) == 2
    assert result.total_price == 0.0


@pytest.mark.asyncio
async def test_load_available_excludes_unavailable(db_with_menu: AsyncSession) -> None:
    """load_available_menu не возвращает позиции с is_available=False."""
    menu = await load_available_menu(db_with_menu, organization_id=1)
    names = [m.name for m in menu]
    assert "Маргарита" not in names
    assert "Плов" in names


@pytest.mark.asyncio
async def test_build_menu_context(db_with_menu: AsyncSession) -> None:
    """build_menu_context формирует текст с ценами и iiko_id."""
    menu = await load_available_menu(db_with_menu, organization_id=1)
    context = build_menu_context(menu)

    assert "Плов" in context
    assert "2790" in context
    assert "[id: uuid-plov]" in context
    assert "Маргарита" not in context


@pytest.mark.asyncio
async def test_build_menu_context_includes_tags(db_with_menu: AsyncSession) -> None:
    """Теги из БД попадают в контекст меню для ИИ."""
    res = await db_with_menu.execute(select(MenuItem).where(MenuItem.name == "Плов"))
    plov = res.scalar_one()
    plov.tags = "хит, к нему: Салат ачичук, чай"
    await db_with_menu.commit()

    menu = await load_available_menu(db_with_menu, organization_id=1)
    context = build_menu_context(menu)
    assert "теги:" in context
    assert "ачичук" in context.lower()


@pytest.mark.asyncio
async def test_validate_with_mock_menu_fallback() -> None:
    """Если menu_items пуст — fallback на MOCK_MENU."""
    items = [OrderItem(name="Плов", quantity=1)]
    result = await validate_order(items, menu_items=[])

    assert len(result.valid_items) == 1
    assert result.total_price == 2790.0


def test_detect_payment_cash_card_remote() -> None:
    assert detect_payment_method_from_text("наличными") == "cash"
    assert detect_payment_method_from_text("Картой привезите терминал") == "card"
    assert detect_payment_method_from_text("каспи переведу") == "remote"
    assert detect_payment_method_from_text("онлайн ссылкой") == "remote"
    assert detect_payment_method_from_text("хммм") is None


def test_detect_payment_multilingual() -> None:
    assert detect_payment_method_from_text("Картамен төлеу") == "card"
    assert detect_payment_method_from_text("By cash please") == "cash"
    assert detect_payment_method_from_text("naqd pul") == "cash"
    assert detect_payment_method_from_text("Payme or Kaspi") == "remote"
    assert detect_payment_method_from_text("payment link") == "remote"


def test_detect_payment_no_false_positive_potato() -> None:
    """«Карт» в составе «картошка» не считаем картой."""
    assert detect_payment_method_from_text("Добавьте картошку фри") is None


def test_build_summary_from_stored_items() -> None:
    j: dict = {
        "items": [
            {"name": "Плов", "quantity": 2, "item_total": 5580.0},
        ],
    }
    s = build_summary_text_from_stored_items(j)
    assert "Плов" in s
    assert "5580" in s


def test_format_whatsapp_order_card_markdown() -> None:
    """Карточка заказа для WhatsApp: разделители и жирный итог."""
    j: dict = {
        "items": [{"name": "Чай", "quantity": 1, "item_total": 500.0}],
        "total_price": 500.0,
        "order_meta": {"order_type": "pickup", "payment_method": "cash"},
    }
    core = build_summary_text_from_stored_items(j)
    text = format_whatsapp_order_card(j, core)
    assert "✨ *Ваш заказ*" in text
    assert "━━━━━━━━━━━━" in text
    assert "Итого к оплате" in text
    assert "500" in text
    assert "*Итого к оплате: 500" in text or "💰 *" in text
    assert "_Готовим для вас с душой" in text


def test_format_draft_order_context_for_prompt_includes_ids() -> None:
    """Phase 18: снимок DRAFT для system prompt — id строк для order_actions."""
    assert format_draft_order_context_for_prompt(None) == ""
    assert format_draft_order_context_for_prompt({"items": []}) == ""
    uid = "11111111-2222-3333-4444-555555555555"
    text = format_draft_order_context_for_prompt({
        "items": [
            {
                "name": "Манты",
                "quantity": 2,
                "iiko_id": uid,
                "category": "Горячее",
                "packaging_plov_1kg": "",
                "exclude_ingredients": ["лук"],
            },
        ],
        "total_price": 4000.0,
        "order_meta": {"order_type": "delivery", "delivery_address": "ул. Абая, 1"},
    })
    assert "Текущий черновик заказа" in text
    assert f"[id: {uid}]" in text
    assert "Манты (Горячее)" in text
    assert "лук" in text
    assert "ул. Абая" in text


def test_format_draft_order_context_includes_fee_lines_summary() -> None:
    """Phase 18 §4.10: в контекст для LLM попадают последние fee_lines и foods_subtotal."""
    text = format_draft_order_context_for_prompt({
        "items": [{"name": "Лагман", "quantity": 1, "iiko_id": "uuid-lagman", "category": "Первое"}],
        "foods_subtotal": 1990.0,
        "fee_lines": [
            {"kind": "packaging_standard", "name": "Контейнер", "quantity": 1, "item_total": 150.0},
            {"kind": "delivery", "name": "Доставка", "quantity": 1, "item_total": 700.0},
        ],
        "total_price": 2840.0,
        "order_meta": {"order_type": "delivery"},
    })
    assert "Сумма по блюдам" in text
    assert "1990" in text
    assert "Доставка" in text
    assert "Контейнер" in text
    assert "compute_fee_lines" in text


def test_merge_cart_actions_add_remove_set() -> None:
    base = [
        {
            "name": "Плов",
            "quantity": 2,
            "price_per_unit": 2790.0,
            "item_total": 5580.0,
            "iiko_id": "uuid-plov",
            "category": "Горячее",
            "packaging_plov_1kg": "",
            "exclude_ingredients": [],
        },
    ]
    out = merge_cart_actions(
        base,
        [
            OrderAction(item_id="Плов", action="remove", quantity=1),
            OrderAction(item_id="Капучино", action="add", quantity=1),
        ],
    )
    assert len(out) == 2
    names_q = {(x["name"], x["quantity"]) for x in out}
    assert ("Плов", 1) in names_q
    cap = next(x for x in out if x["name"] == "Капучино")
    assert cap["quantity"] == 1

    cleared = merge_cart_actions(out, [OrderAction(item_id="Капучино", action="set_quantity", quantity=0)])
    assert len(cleared) == 1
    assert cleared[0]["name"] == "Плов"


def test_filter_order_actions_idempotent_explicit_id() -> None:
    actions = [
        OrderAction(action_id="a1", item_id="uuid-x", action="add", quantity=1),
        OrderAction(action_id="a1", item_id="uuid-x", action="add", quantity=9),
        OrderAction(action_id="a2", item_id="uuid-y", action="add", quantity=1),
    ]
    out, new_ids = filter_order_actions_idempotent(
        actions,
        applied=set(),
        inbound_message_id="wa-1",
    )
    assert len(out) == 2
    assert new_ids == ["a1", "a2"]
    out2, ids2 = filter_order_actions_idempotent(
        actions,
        applied={"a1", "a2"},
        inbound_message_id="wa-1",
    )
    assert out2 == []
    assert ids2 == []


def test_compute_order_action_stable_id_without_explicit() -> None:
    a = OrderAction(item_id="uuid-cap", action="add", quantity=2)
    id1 = compute_order_action_stable_id(a, 0, "msg-99")
    id2 = compute_order_action_stable_id(a, 0, "msg-99")
    id3 = compute_order_action_stable_id(a, 1, "msg-99")
    assert id1 == id2
    assert id1 != id3


def test_merge_applied_order_action_ids_resets() -> None:
    ij: dict = {"order_meta": {"applied_order_action_ids": ["x", "y"], "k": 1}}
    out = merge_applied_order_action_ids_into_items_json(ij, new_ids=[], reset=True)
    assert (out.get("order_meta") or {}).get("applied_order_action_ids") == []


def test_merge_cart_actions_resolve_by_iiko_id_string() -> None:
    """Сопоставление по iiko_id даже если это не UUID (как в тестовом меню)."""
    base = [
        {
            "name": "Лагман",
            "quantity": 1,
            "price_per_unit": 1990.0,
            "item_total": 1990.0,
            "iiko_id": "uuid-lagman",
            "category": "Первое",
            "packaging_plov_1kg": "",
            "exclude_ingredients": [],
        },
    ]
    out = merge_cart_actions(base, [OrderAction(item_id="uuid-lagman", action="add", quantity=2)])
    assert len(out) == 1
    assert out[0]["quantity"] == 3


def test_merge_cart_actions_real_uuid_stub_then_enrich() -> None:
    uid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    menu = [
        MenuItem(
            name="Блюдо UUID",
            category="Тест",
            price=500.0,
            is_available=True,
            iiko_id=uid,
        )
    ]
    merged = merge_cart_actions([], [OrderAction(item_id=uid, action="add", quantity=2)])
    assert merged[0].get("name") == ""
    enriched = enrich_merged_items_from_menu(merged, menu)
    assert enriched[0]["name"] == "Блюдо UUID"
    assert enriched[0]["quantity"] == 2
    oi = draft_food_lines_to_order_items(enriched)
    assert len(oi) == 1
    assert oi[0].name == "Блюдо UUID"
    assert oi[0].quantity == 2


@pytest.mark.asyncio
async def test_calculate_total_and_fees_wraps_validate_and_finalize(db_with_menu: AsyncSession) -> None:
    """calculate_total_and_fees — обёртка validate_order + finalize_order_draft."""
    menu = await load_available_menu(db_with_menu, organization_id=1)
    food = [
        {
            "name": "Плов",
            "quantity": 1,
            "price_per_unit": 2790.0,
            "item_total": 2790.0,
            "iiko_id": "uuid-plov",
            "category": "Горячее",
            "packaging_plov_1kg": "",
            "exclude_ingredients": [],
        },
    ]
    ai = AIBrainResponse(
        intent="order",
        reply_text="тест",
        items=[],
        order_type="delivery",
        payment_method="cash",
    )
    validated, items_json, total = await calculate_total_and_fees(
        food, ai, menu_items=menu, db=db_with_menu,
    )
    assert len(validated.valid_items) == 1
    assert "fee_lines" in items_json
    assert total > 0


def test_merge_remove_plov_line_packaging_field_preserved_until_revalidate() -> None:
    """После remove строка исчезает — пересчёт fee_lines делает не merge, а пайплайн заказа."""
    base = [
        {
            "name": "Плов 1кг баранина",
            "quantity": 1,
            "iiko_id": "p1",
            "category": "Горячее",
            "packaging_plov_1kg": "foil_kazan",
            "price_per_unit": 5000.0,
            "item_total": 5000.0,
            "exclude_ingredients": [],
        },
    ]
    out = merge_cart_actions(base, [OrderAction(item_id="Плов 1кг баранина", action="remove", quantity=1)])
    assert out == []
