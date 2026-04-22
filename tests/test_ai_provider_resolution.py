"""
Тесты выбора AI-провайдера и контракта `call_ai` на ошибки транспорта.

Покрываем два независимых инварианта AI Brain:

1) `_resolve_provider_name()` — чистая функция маршрутизации.
   Решение зависит от связки (AI_PROVIDER, has_openai_key, has_gemini_key)
   и должно быть детерминированным. Это «мозг над мозгом» — если он сломан,
   упадёт весь AI-стек, поэтому таблица решений закрыта параметризованным
   тестом с явными ожиданиями.

2) `call_ai(..., raise_on_transient=...)` — контракт проброса транспортных
   ошибок. ARQ-воркер хочет «громкий» TransientAiError (чтобы уйти в retry),
   синхронные места (админка, preview) хотят тихий fallback-escalate.
   Контракт один на два мира — и должен быть зафиксирован тестом.

Архитектурный принцип: мокаем только границы домена (Settings и провайдер),
но не внутреннюю логику `ai_brain`. Это ловит регрессии в самой логике, а не
в моках.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.ai_schemas import AIBrainResponse
from app.services import ai_brain
from app.services.ai_engine.errors import TransientAiError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_provider_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Гарантируем чистое состояние между тестами:

    - сбрасываем кеш лог-решения (иначе WARNING/INFO посчитают вход «тем же»
      и пропустят логирование — тесты станут зависеть от порядка запуска);
    - сбрасываем кеш провайдер-инстансов (иначе тест, который первым создал
      OpenAIProvider без ключа, «заразит» следующие тесты тем же объектом).
    """
    ai_brain._reset_provider_choice_log()
    monkeypatch.setattr(ai_brain, "_openai_provider", None)
    monkeypatch.setattr(ai_brain, "_gemini_provider", None)


def _set_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ai_provider: str,
    openai_key: str,
    gemini_key: str,
) -> None:
    """
    Подменяем поля `settings` точечно, а не весь объект. Это безопаснее: если
    в `settings` появится новое поле, тест не упадёт на `AttributeError`.
    """
    monkeypatch.setattr(ai_brain.settings, "ai_provider", ai_provider, raising=False)
    monkeypatch.setattr(ai_brain.settings, "openai_api_key", openai_key, raising=False)
    monkeypatch.setattr(ai_brain.settings, "gemini_api_key", gemini_key, raising=False)


# ---------------------------------------------------------------------------
# 1) Provider resolution decision table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ai_provider", "openai_key", "gemini_key", "expected", "scenario"),
    [
        # Прямые выборы — ключ на месте, берём запрошенное.
        ("openai", "sk-x", "",      "openai", "direct_openai"),
        ("gemini", "",     "g-x",   "gemini", "direct_gemini"),

        # Graceful fallback: запрошенный провайдер без ключа, альтернатива — с ключом.
        ("gemini", "sk-x", "",      "openai", "fallback_gemini_to_openai"),
        ("openai", "",     "g-x",   "gemini", "fallback_openai_to_gemini"),

        # Дефолт: мусор в AI_PROVIDER при обоих ключах — возвращаем openai (BC).
        ("unknown", "sk-x", "g-x",  "openai", "default_when_unknown"),

        # «Слепой» случай: ключей нет вообще. Сознательно возвращаем ЗАПРОШЕННЫЙ
        # провайдер (а не None/raise), чтобы downstream честно залогировал NO_KEY.
        # Это отличается от «None/Error» из исходной матрицы задачи — см. docstring
        # `_resolve_provider_name`, пункт 4: не маскируем конфиг-ошибку.
        ("openai", "", "", "openai", "blind_returns_requested"),

        # Дополнительные инварианты — не в исходной матрице, но критичны:
        # пустой AI_PROVIDER → авто-выбор по единственному доступному ключу.
        ("",        "sk-x", "",    "openai", "auto_pick_openai_when_only_key"),
        ("",        "",     "g-x", "gemini", "auto_pick_gemini_when_only_key"),
    ],
)
def test_resolve_provider_name_decision_table(
    monkeypatch: pytest.MonkeyPatch,
    ai_provider: str,
    openai_key: str,
    gemini_key: str,
    expected: str,
    scenario: str,
) -> None:
    _set_settings(
        monkeypatch,
        ai_provider=ai_provider,
        openai_key=openai_key,
        gemini_key=gemini_key,
    )

    assert ai_brain._resolve_provider_name() == expected, f"scenario={scenario}"


def test_resolve_logs_warning_on_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Контракт наблюдаемости: когда прод «тихо» переключается с запрошенного
    провайдера на альтернативу, должен быть WARNING — это сигнал для алертов.
    """
    _set_settings(monkeypatch, ai_provider="gemini", openai_key="sk-x", gemini_key="")

    with caplog.at_level("WARNING", logger="app.services.ai_brain"):
        chosen = ai_brain._resolve_provider_name()

    assert chosen == "openai"
    assert any(
        "AI_PROVIDER=gemini" in rec.getMessage() and "fallback" in rec.getMessage()
        for rec in caplog.records
    ), "ожидался WARNING про fallback"


# ---------------------------------------------------------------------------
# 2) call_ai — контракт проброса TransientAiError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_ai_raises_transient_when_flag_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ARQ-воркер полагается на `raise_on_transient=True` (дефолт): если
    провайдер кинул TransientAiError — исключение должно долететь до воркера,
    чтобы он ушёл в retry с backoff.
    """
    _set_settings(monkeypatch, ai_provider="openai", openai_key="sk-x", gemini_key="")

    fake_provider = AsyncMock()
    fake_provider.generate_response = AsyncMock(side_effect=TransientAiError("boom"))
    monkeypatch.setattr(ai_brain, "get_ai_client", lambda: fake_provider)

    with pytest.raises(TransientAiError):
        await ai_brain.call_ai(history=[], user_text="hi", raise_on_transient=True)

    assert fake_provider.generate_response.await_count == 1


@pytest.mark.asyncio
async def test_call_ai_returns_escalate_when_flag_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Синхронные вызовы (админка, preview) выбирают `raise_on_transient=False`:
    ошибка провайдера должна превратиться в штатный escalate-ответ, а не
    «взорваться» наружу. UI обязан показать что-то клиенту.
    """
    _set_settings(monkeypatch, ai_provider="openai", openai_key="sk-x", gemini_key="")

    fake_provider = AsyncMock()
    fake_provider.generate_response = AsyncMock(side_effect=TransientAiError("boom"))
    monkeypatch.setattr(ai_brain, "get_ai_client", lambda: fake_provider)

    result = await ai_brain.call_ai(history=[], user_text="hi", raise_on_transient=False)

    assert isinstance(result, AIBrainResponse)
    assert result.intent == "escalate"
    assert ai_brain.is_openai_fallback_escalation_reply(result.reply_text)


@pytest.mark.asyncio
async def test_call_ai_passes_through_successful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Happy path: провайдер вернул AIBrainResponse — `call_ai` отдаёт его как
    есть, без оборачивания/модификации. Это «негативный» тест на регрессию:
    гарантия, что диспетчер не «переделает» успешный ответ.
    """
    _set_settings(monkeypatch, ai_provider="openai", openai_key="sk-x", gemini_key="")

    expected = AIBrainResponse(intent="faq", reply_text="hello")
    fake_provider = AsyncMock()
    fake_provider.generate_response = AsyncMock(return_value=expected)
    monkeypatch.setattr(ai_brain, "get_ai_client", lambda: fake_provider)

    result = await ai_brain.call_ai(history=[], user_text="hi")

    assert result is expected
