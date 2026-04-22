"""Тесты нормализации Markdown → WhatsApp-текст (_strip_markdown_for_whatsapp)."""

from __future__ import annotations

import pytest

from app.integrations.whatsapp import _strip_markdown_for_whatsapp


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**Чайханский плов**", "*Чайханский плов*"),
        ("Рекомендую **плов** и **манты**.", "Рекомендую *плов* и *манты*."),
        ("__важно__", "_важно_"),
        ("## Заголовок\nТекст", "Заголовок\nТекст"),
        ("Цена `1990` тенге", "Цена 1990 тенге"),
        ("```json\n{\"a\":1}\n```", '{"a":1}\n'),
        ("Смотри [меню](https://x.com)", "Смотри меню (https://x.com)"),
        ("[только метка]()", "только метка"),
    ],
)
def test_strip_markdown_cases(raw: str, expected: str) -> None:
    assert _strip_markdown_for_whatsapp(raw) == expected


def test_strip_preserves_single_star_bold() -> None:
    """Уже корректный WA-формат не ломаем."""
    s = "Попробуйте *лагман* сегодня."
    assert _strip_markdown_for_whatsapp(s) == s


def test_strip_empty_and_whitespace() -> None:
    assert _strip_markdown_for_whatsapp("") == ""
    assert _strip_markdown_for_whatsapp("   ") == "   "
