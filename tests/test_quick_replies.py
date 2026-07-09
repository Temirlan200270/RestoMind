"""Тесты quick_replies (LLM bypass)."""

from types import SimpleNamespace

import pytest

from app.db.models import Organization
from app.services.dialog_mgr import UserState
from app.services.quick_replies import (
    _build_menu_probe_reply_from_items,
    build_menu_probe_quick_reply_text,
    build_recommendation_quick_reply_text,
    is_plain_greeting,
    peek_quick_reply_trigger,
    try_quick_reply,
)


@pytest.mark.asyncio
async def test_greeting_plain() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="привет",
        state=UserState.CHATTING,
        has_open_draft=False,
    )
    assert hit is not None
    assert hit.template_id == "greeting_plain"
    assert "Что бы вы хотели заказать" in hit.reply_text
    assert "подсказать по меню" in hit.reply_text


@pytest.mark.asyncio
async def test_thanks() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="спасибо",
        state=UserState.CHATTING,
        has_open_draft=False,
    )
    assert hit is not None
    assert hit.template_id == "thanks"


@pytest.mark.asyncio
async def test_operator_request() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="оператор",
        state=UserState.CHATTING,
        has_open_draft=False,
    )
    assert hit is not None
    assert hit.template_id == "operator_request"
    assert hit.set_human_mode is True
    assert "alert_operator_telegram" in hit.side_effects


@pytest.mark.asyncio
async def test_cancel_without_draft_returns_none() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="отмена",
        state=UserState.CHATTING,
        has_open_draft=False,
    )
    assert hit is None


@pytest.mark.asyncio
async def test_cancel_with_draft() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="отмена",
        state=UserState.CHATTING,
        has_open_draft=True,
    )
    assert hit is not None
    assert hit.template_id == "cancel_order"
    assert "cancel_open_draft" in hit.side_effects


@pytest.mark.asyncio
async def test_long_message_no_match() -> None:
    long_text = "отмените пожалуйста тот плов и добавьте ещё манты три порции"
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text=long_text,
        state=UserState.CHATTING,
        has_open_draft=True,
    )
    assert hit is None


@pytest.mark.asyncio
async def test_working_hours_needs_org() -> None:
    org = Organization(id=1, name="T", slug="t", timezone="Asia/Almaty")
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="время работы",
        state=UserState.CHATTING,
        has_open_draft=False,
        org=org,
    )
    assert hit is not None
    assert hit.template_id == "working_hours"
    assert "Сегодня" in hit.reply_text or "выходной" in hit.reply_text.lower()


def test_is_plain_greeting_rejects_menu_intent() -> None:
    assert is_plain_greeting("привет, меню") is False


def test_problem_dialog_short_messages_trigger_quick_reply_paths() -> None:
    assert peek_quick_reply_trigger("Здравствуйте, что посоветуешь?") == "recommendation_request"
    assert peek_quick_reply_trigger("Что ещё?") == "recommendation_request"
    assert peek_quick_reply_trigger("Что еще?") == "recommendation_request"
    assert peek_quick_reply_trigger("Подскажите") == "recommendation_request"
    assert peek_quick_reply_trigger("Мясное давай") == "menu_probe"
    assert peek_quick_reply_trigger("Плов") == "menu_probe"
    assert peek_quick_reply_trigger("Плов есть?") == "menu_probe"
    assert peek_quick_reply_trigger("Плов какой есть?") == "menu_probe"


def test_menu_probe_reply_from_items_for_problem_dialog() -> None:
    items = [
        SimpleNamespace(
            name="Плов праздничный баранина",
            category="Традиционная кухня",
            tags="",
            price=2790,
            is_available=True,
        ),
        SimpleNamespace(
            name="Плов праздничный говядина",
            category="Традиционная кухня",
            tags="",
            price=2890,
            is_available=True,
        ),
        SimpleNamespace(
            name="Казан кебаб с говядина",
            category="Мясные блюда",
            tags="",
            price=4490,
            is_available=True,
        ),
        SimpleNamespace(
            name="Лимонад",
            category="Напитки",
            tags="",
            price=990,
            is_available=True,
        ),
    ]

    plov_reply = _build_menu_probe_reply_from_items(items, "Плов какой есть?")
    meat_reply = _build_menu_probe_reply_from_items(items, "Мясное давай")

    assert plov_reply is not None
    assert "Плов праздничный баранина" in plov_reply
    assert "Плов праздничный говядина" in plov_reply
    assert "Какой добавить" in plov_reply
    assert "не успел обработать" not in plov_reply
    assert meat_reply is not None
    assert "Из мясного" in meat_reply
    assert "Казан кебаб" in meat_reply


@pytest.mark.asyncio
async def test_menu_request_uses_human_category_labels(db_with_menu) -> None:
    from app.db.models import MenuItem
    from app.services.quick_replies import build_menu_quick_reply_text

    db_with_menu.add(
        MenuItem(
            organization_id=1,
            name="Тестовая самса",
            price=400,
            category="Выпечка-1",
            is_available=True,
        ),
    )
    await db_with_menu.flush()

    preview = await build_menu_quick_reply_text(db_with_menu, 1)
    assert "Выпечка-1" not in preview
    assert "• Выпечка:" in preview or "• Горячие блюда:" in preview


@pytest.mark.asyncio
async def test_menu_request(db_with_menu) -> None:
    from app.services.quick_replies import build_menu_quick_reply_text

    preview = await build_menu_quick_reply_text(db_with_menu, 1)
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="меню",
        state=UserState.CHATTING,
        has_open_draft=False,
        menu_preview=preview,
    )
    assert hit is not None
    assert hit.template_id == "menu_request"
    assert "Плов" in hit.reply_text or "меню" in hit.reply_text.lower()


@pytest.mark.asyncio
async def test_recommendation_request_returns_real_menu_items(db_with_menu) -> None:
    preview = await build_recommendation_quick_reply_text(db_with_menu, 1)
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="Что посоветуете?",
        state=UserState.CHATTING,
        has_open_draft=False,
        recommendation_preview=preview,
    )
    assert hit is not None
    assert hit.template_id == "recommendation_request"
    assert "Из популярного могу посоветовать" in hit.reply_text
    assert "Плов" in hit.reply_text
    assert "Что добавить в заказ" in hit.reply_text
    assert "С радостью помогу оформить заказ" not in hit.reply_text


@pytest.mark.asyncio
async def test_short_hint_podskazhite_returns_recommendations(db_with_menu) -> None:
    preview = await build_recommendation_quick_reply_text(db_with_menu, 1)
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="Подскажите",
        state=UserState.CHATTING,
        has_open_draft=False,
        recommendation_preview=preview,
    )

    assert hit is not None
    assert hit.template_id == "recommendation_request"
    assert "Из популярного могу посоветовать" in hit.reply_text


@pytest.mark.asyncio
async def test_more_recommendations_followup_uses_preloaded_preview_without_db() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="Что ещё?",
        state=UserState.CHATTING,
        has_open_draft=False,
        recommendation_preview="Из популярного могу посоветовать:\n• Плов — 2790 ₸",
    )

    assert hit is not None
    assert hit.template_id == "recommendation_request"
    assert "Плов" in hit.reply_text
    assert "не успел обработать" not in hit.reply_text


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Что ещё?", "Что еще?", "Ещё варианты"])
async def test_more_recommendations_followup_does_not_use_llm(db_with_menu, message: str) -> None:
    preview = await build_recommendation_quick_reply_text(db_with_menu, 1)
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text=message,
        state=UserState.CHATTING,
        has_open_draft=False,
        recommendation_preview=preview,
    )

    assert hit is not None
    assert hit.template_id == "recommendation_request"
    assert "Из популярного могу посоветовать" in hit.reply_text
    assert "не успел обработать" not in hit.reply_text


@pytest.mark.asyncio
async def test_meat_hint_returns_menu_probe_without_llm(db_with_menu) -> None:
    text = await build_menu_probe_quick_reply_text(db_with_menu, 1, "Мясное давай")
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="Мясное давай",
        state=UserState.CHATTING,
        has_open_draft=False,
        menu_probe_text=text,
    )

    assert hit is not None
    assert hit.template_id == "menu_probe"
    assert "Из мясного" in hit.reply_text
    assert "Что добавить" in hit.reply_text


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Плов", "Плов есть?", "Плов какой есть?"])
async def test_plov_probe_returns_variants_without_llm(db_with_menu, message: str) -> None:
    text = await build_menu_probe_quick_reply_text(db_with_menu, 1, message)
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text=message,
        state=UserState.CHATTING,
        has_open_draft=False,
        menu_probe_text=text,
    )

    assert hit is not None
    assert hit.template_id == "menu_probe"
    assert "Плов" in hit.reply_text
    assert "Добавить" in hit.reply_text or "Какой добавить" in hit.reply_text
    assert "не успел обработать" not in hit.reply_text


@pytest.mark.asyncio
async def test_order_status_with_draft(db_with_menu) -> None:
    from app.db.models import Order, OrderStatus, User
    from app.services.quick_replies import build_order_status_quick_reply_text

    user = User(organization_id=1, phone="+77001112233", name="T")
    db_with_menu.add(user)
    await db_with_menu.flush()
    draft = Order(
        organization_id=1,
        user_id=user.id,
        status=OrderStatus.DRAFT,
        total_price=2790.0,
        items_json={"items": [{"name": "Плов", "qty": 1}]},
    )
    db_with_menu.add(draft)
    await db_with_menu.flush()

    status_text = await build_order_status_quick_reply_text(
        db_with_menu,
        phone="+77001112233",
        organization_id=1,
        draft_row=draft,
    )
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="статус заказа",
        state=UserState.CHATTING,
        has_open_draft=True,
        order_status_text=status_text,
    )
    assert hit is not None
    assert hit.template_id == "order_status"
    assert "Плов" in hit.reply_text
    assert "черновик" in hit.reply_text.lower()
