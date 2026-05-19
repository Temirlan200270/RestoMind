"""GuestCare External — импорт внешних отзывов (2GIS / Google) MVP."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


def detect_review_source(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if "2gis" in host:
        return "2gis"
    if "google" in host or "g.page" in host:
        return "google"
    return "external"


def import_review_from_url(url: str, *, note: str | None = None) -> dict[str, Any]:
    """MVP: сохраняем метаданные URL; полный парсинг API — в следующей итерации."""
    url_s = (url or "").strip()
    if not url_s or not re.match(r"^https?://", url_s, re.I):
        raise ValueError("Укажите корректный URL (https://…)")
    rid = hashlib.sha256(url_s.encode()).hexdigest()[:16]
    source = detect_review_source(url_s)
    return {
        "id": rid,
        "source": source,
        "url": url_s,
        "author": "Гость",
        "rating": None,
        "text": note or f"Импортировано из {source}",
        "imported_at": datetime.now(tz=timezone.utc).isoformat(),
        "reply_draft": None,
    }


def draft_reply_for_review(review: dict[str, Any], *, tone: str = "friendly") -> str:
    """Простой черновик ответа без LLM (MVP)."""
    rating = review.get("rating")
    name = (review.get("author") or "Гость").strip()
    if rating is not None and int(rating) <= 2:
        return (
            f"Здравствуйте, {name}! Нам очень жаль, что опыт не оправдал ожиданий. "
            "Напишите нам в WhatsApp — разберём ситуацию и предложим решение."
        )
    return (
        f"Спасибо, {name}, за отзыв! Рады, что вы выбрали нас. "
        "Будем рады видеть вас снова."
    )
