from types import SimpleNamespace

from app.api.webhooks import _quick_reply_allowed_before_llm, _soft_ai_unavailable_response
from app.services.quick_replies import peek_quick_reply_trigger


def test_soft_ai_fallback_answers_menu_probe_without_operator() -> None:
    response = _soft_ai_unavailable_response(
        message_text="Плов есть?",
        menu_items=[
            SimpleNamespace(name="Плов праздничный", category="Горячее", tags="", price=2790, is_available=True),
            SimpleNamespace(name="Лагман", category="Горячее", tags="", price=2890, is_available=True),
        ],
        has_draft=False,
    )

    assert response.intent == "faq"
    assert "Плов праздничный" in response.reply_text
    assert "оператор" not in response.reply_text.lower()
    assert "не успел обработать" not in response.reply_text.lower()
    assert "техническ" not in response.reply_text.lower()


def test_soft_ai_fallback_answers_more_recommendations_without_operator() -> None:
    response = _soft_ai_unavailable_response(
        message_text="Что ещё?",
        menu_items=[
            SimpleNamespace(name="Ган-фан", category="Горячее", tags="хит", price=2790, is_available=True),
            SimpleNamespace(name="Казан кебаб", category="Мясное", tags="", price=4490, is_available=True),
        ],
        has_draft=False,
    )

    assert response.intent == "faq"
    assert "Из популярного могу посоветовать" in response.reply_text
    assert "оператор" not in response.reply_text.lower()
    assert "техническ" not in response.reply_text.lower()


def test_soft_ai_fallback_with_draft_asks_for_order_clarification() -> None:
    response = _soft_ai_unavailable_response(
        message_text="убери один лагман",
        menu_items=[
            SimpleNamespace(name="Ган-фан", category="Горячее", tags="хит", price=2790, is_available=True),
        ],
        has_draft=True,
    )

    assert response.intent == "faq"
    assert "продолжу заказ" in response.reply_text
    assert "добавить или убрать блюдо" in response.reply_text
    assert "Ган-фан" not in response.reply_text
    assert "оператор" not in response.reply_text.lower()


def test_menu_and_order_talk_stays_llm_first() -> None:
    for text in ["Здравствуйте, что посоветуешь?", "Что ещё?", "Плов есть?", "Меню", "Мясное давай"]:
        assert _quick_reply_allowed_before_llm(peek_quick_reply_trigger(text)) is False

    for text in ["спасибо", "оператор", "статус заказа"]:
        assert _quick_reply_allowed_before_llm(peek_quick_reply_trigger(text)) is True
