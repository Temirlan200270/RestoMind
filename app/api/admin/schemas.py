"""Общие Pydantic-модели для админ-API (E0.1)."""

from __future__ import annotations

from pydantic import BaseModel


class TextRequest(BaseModel):
    """Тело запроса с текстовым сообщением (чат оператора, тест бота)."""

    text: str
