"""
Отслеживание смены стоп-листа в рамках диалога (org + phone).

Позволяет отличить «давно на стопе» от «только что ушло на стоп» и дать человечный ответ.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db.models import MenuItem

logger = logging.getLogger(__name__)

_STOPLIST_SEEN_TTL = 86400  # 24 ч, как история диалога


def _redis_key(organization_id: int, phone: str) -> str:
    return f"rm:stoplist_seen:{int(organization_id)}:{phone}"


def _item_stop_key(item: MenuItem) -> str:
    return (item.iiko_id or str(item.id) or item.name or "").strip()


def stopped_keys(menu_items: list[MenuItem]) -> set[str]:
    return {_item_stop_key(m) for m in menu_items if not m.is_available and _item_stop_key(m)}


def stopped_names_by_key(menu_items: list[MenuItem]) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in menu_items:
        if m.is_available:
            continue
        k = _item_stop_key(m)
        if k:
            out[k] = (m.name or k).strip()
    return out


async def load_seen_stopped_keys(redis: Any, phone: str, organization_id: int) -> set[str]:
    try:
        raw = await redis.get(_redis_key(organization_id, phone))
        if not raw:
            return set()
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        data = json.loads(raw)
        if isinstance(data, list):
            return {str(x) for x in data if x}
    except Exception as exc:
        logger.debug("stoplist_seen load: %s", exc)
    return set()


async def save_seen_stopped_keys(
    redis: Any,
    phone: str,
    organization_id: int,
    menu_items: list[MenuItem],
) -> None:
    keys = sorted(stopped_keys(menu_items))
    try:
        await redis.set(
            _redis_key(organization_id, phone),
            json.dumps(keys, ensure_ascii=False),
            ex=_STOPLIST_SEEN_TTL,
        )
    except Exception as exc:
        logger.warning("stoplist_seen save org=%s: %s", organization_id, exc)


def newly_stopped_names(
    previous_keys: set[str],
    menu_items: list[MenuItem],
) -> list[str]:
    """Позиции, которые только что попали на стоп (не были в снимке прошлого сообщения)."""
    catalog = stopped_names_by_key(menu_items)
    current = set(catalog)
    fresh_keys = current - previous_keys
    return [catalog[k] for k in sorted(fresh_keys, key=lambda x: catalog[x].lower())]


def format_stoplist_change_for_menu_context(
    newly_stopped: list[str],
    *,
    draft_overlap: list[str],
) -> str:
    if not newly_stopped:
        return ""
    lines = [
        "# Только что изменился стоп-лист (критично для честного ответа)",
        "Следующие позиции **только что** попали на стоп (в прошлой реплике могли быть в наличии):",
    ]
    for name in newly_stopped:
        lines.append(f"- {name}")
    if draft_overlap:
        overlap = ", ".join(f"«{x}»" for x in draft_overlap)
        lines.append(
            f"В черновике заказа клиента были: {overlap}. "
            "Сервер уберёт их из корзины — коротко извинись и предложи замену."
        )
    lines.append(
        "Не утверждай, что эти блюда «есть в наличии». "
        "Формулировка: к сожалению, сейчас на стопе / только что закончилось."
    )
    return "\n".join(lines)


def _norm_name(name: str) -> str:
    return (name or "").lower().strip()


def compose_stoplist_notice(
    stoplist_items: list[str],
    newly_stopped: list[str],
    *,
    draft_item_names: list[str] | None = None,
) -> str:
    """
    Текст для клиента при отклонении стоп-позиций.
    Различает «только что на стопе» и «уже было на стопе».
    """
    if not stoplist_items:
        return ""
    fresh = {_norm_name(x) for x in newly_stopped}
    draft_set = {_norm_name(x) for x in (draft_item_names or [])}
    parts: list[str] = []
    for name in stoplist_items:
        n = _norm_name(name)
        if n in fresh:
            if n in draft_set:
                parts.append(
                    f"К сожалению, «{name}» только что ушло на стоп — убрал из вашего заказа."
                )
            else:
                parts.append(
                    f"К сожалению, «{name}» только что попало на стоп — добавить не получится."
                )
        else:
            parts.append(f"«{name}» сейчас временно недоступно (на стопе).")
    if not parts:
        return ""
    tail = " Могу подсказать, чем заменить — напишите, что хотите."
    return "\n".join(parts) + tail


def draft_names_on_stop(
    draft_items: list[dict],
    stoplist_items: list[str],
) -> list[str]:
    """Имена из черновика, которые сейчас на стопе."""
    stop_set = {_norm_name(x) for x in stoplist_items}
    out: list[str] = []
    for row in draft_items:
        if not isinstance(row, dict):
            continue
        nm = str(row.get("name") or "").strip()
        if nm and _norm_name(nm) in stop_set:
            out.append(nm)
    return out
