"""
Тесты лояльной валидации AIBrainResponse к `null`-полям от LLM.

Контекст: в проде наблюдалось, что Gemini (и потенциально любой LLM) заполняет
все поля схемы `null`, включая не-nullable поля с дефолтом (Literal, bool,
nested model). Стандартный Pydantic корректно падает на это с ValidationError
— но это делает бота неработоспособным на простейшем faq-ответе.

Решение: `model_validator(mode="before")` в AIBrainResponse schrinkует `null`
в белом списке полей до «ключ отсутствует», чтобы сработал default. Белый
список фиксирован и видим — новые nullable-поля должны явно добавляться.

Эти тесты закрывают:
1) Coercion работает для каждого поля из белого списка (параметризация).
2) Coercion НЕ трогает поля, не входящие в белый список (intent, reply_text).
3) Реальный «боевой» ответ из прод-лога парсится без ошибок.
4) Комбинация с `_normalize_payment` (после валидации) не ломается.
"""

from __future__ import annotations

import pytest

from app.schemas.ai_schemas import AIBrainResponse, PaymentSplit


_MINIMAL_VALID: dict[str, object] = {
    "intent": "faq",
    "reply_text": "hello",
}


@pytest.mark.parametrize(
    ("field", "expected_default"),
    [
        ("order_type", ""),
        ("payment_method", ""),
        ("payment_mode", "single"),
        ("is_preorder", False),
        ("delivery_address", ""),
        ("pickup_time_note", ""),
        ("detected_language", "ru"),
        ("is_recommendation", False),
        ("items", []),
        ("order_actions", []),
        ("rejected_upsell_iiko_ids", []),
    ],
)
def test_null_collapses_to_default_for_whitelisted_field(
    field: str, expected_default: object,
) -> None:
    """
    Gemini часто пишет `null` вместо пропуска ключа. Для полей с дефолтом
    это должно превращаться в дефолт, а не в ValidationError.
    """
    payload = {**_MINIMAL_VALID, field: None}

    result = AIBrainResponse.model_validate(payload)

    assert getattr(result, field) == expected_default


def test_payment_split_null_collapses_to_default_factory() -> None:
    """
    payment_split — nested model, проверяем отдельно: должен вернуться
    пустой PaymentSplit (все нули), а не None.
    """
    payload = {**_MINIMAL_VALID, "payment_split": None}

    result = AIBrainResponse.model_validate(payload)

    assert isinstance(result.payment_split, PaymentSplit)
    assert result.payment_split.cash == 0.0
    assert result.payment_split.card == 0.0
    assert result.payment_split.remote == 0.0


def test_payment_split_non_object_collapses_to_default_factory() -> None:
    payload = {**_MINIMAL_VALID, "payment_split": "single"}

    result = AIBrainResponse.model_validate(payload)

    assert isinstance(result.payment_split, PaymentSplit)
    assert result.payment_split.cash == 0.0
    assert result.payment_split.card == 0.0
    assert result.payment_split.remote == 0.0


def test_null_in_required_field_still_fails() -> None:
    """
    Coercion НЕ должен маскировать ошибки в required-полях без дефолта.
    `intent` — обязательное, `None` там — реальная ошибка схемы,
    не баг LLM-сериализации.
    """
    payload = {"intent": None, "reply_text": "hi"}

    with pytest.raises(Exception):
        AIBrainResponse.model_validate(payload)


def test_real_gemini_payload_from_prod_log_parses() -> None:
    """
    Реплей прод-лога: ответ Gemini, который падал с
    order_type[literal_error], payment_split[model_type] и т.д.

    После фикса схема должна принять этот payload без исключений и
    подставить дефолты в проблемные поля.
    """
    payload = {
        "intent": "faq",
        "reply_text": "Здравствуйте! Что бы вы хотели заказать? Могу подсказать по меню и помочь оформить заказ.",
        "detected_language": "ru",
        "items": [],
        "order_actions": [],
        "is_recommendation": False,
        "upsell_offered": None,
        "upsell_offered_id": None,
        "upsell_reasoning": None,
        "order_type": None,
        "payment_method": None,
        "payment_mode": None,
        "payment_split": None,
        "is_preorder": None,
        "delivery_address": None,
        "pickup_time_note": None,
    }

    result = AIBrainResponse.model_validate(payload)

    assert result.intent == "faq"
    assert result.reply_text == "Здравствуйте! Что бы вы хотели заказать? Могу подсказать по меню и помочь оформить заказ."
    # Все «проблемные» поля — в дефолтах:
    assert result.order_type == ""
    assert result.payment_method == ""
    assert result.payment_mode == "single"
    assert isinstance(result.payment_split, PaymentSplit)
    assert result.is_preorder is False
    assert result.delivery_address == ""
    assert result.pickup_time_note == ""


def test_coercion_does_not_touch_non_whitelisted_nullable_fields() -> None:
    """
    Поля, которые легитимно nullable (`upsell_offered`, `recognized_speech` и т.д.)
    должны сохранять None как валидное значение — это их доменная семантика.
    """
    payload = {
        **_MINIMAL_VALID,
        "upsell_offered": None,
        "upsell_offered_id": None,
        "booking_details": None,
        "booking_time": None,
        "recognized_speech": None,
    }

    result = AIBrainResponse.model_validate(payload)

    assert result.upsell_offered is None
    assert result.upsell_offered_id is None
    assert result.booking_details is None
    assert result.booking_time is None
    assert result.recognized_speech is None


def test_payment_split_post_normalization_still_works() -> None:
    """
    Коэрсия работает «до» валидации, а `_normalize_payment` — «после».
    Проверяем, что они не конфликтуют: если пришёл null payment_split
    при payment_mode=single — финальный payment_split всё равно
    «обнуляется» через _normalize_payment (в рамках своего контракта).
    """
    payload = {
        **_MINIMAL_VALID,
        "payment_mode": "single",
        "payment_split": None,
    }

    result = AIBrainResponse.model_validate(payload)

    assert result.payment_mode == "single"
    assert result.payment_split.cash == 0.0
