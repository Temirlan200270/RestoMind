"""
Контекст гостя для персонализации ответов ИИ (вау-пакет: память + тон).
Собирается на сервере и подмешивается в system prompt — без лишних ПД в логах.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, User


async def build_customer_context(db: AsyncSession, user: User | None) -> str:
    """
    Краткое «досье» для system prompt: имя, заметка оператора, последние заказы (до 3).
    Пустая строка — новый гость или нет данных.
    """
    if user is None:
        return ""

    parts: list[str] = []

    display = (user.name or "").strip()
    if display:
        parts.append(f"ИМЯ В БАЗЕ: {display}")

    note = (user.operator_note or "").strip()
    if note:
        parts.append(f"ЗАМЕТКА ОПЕРАТОРА (учитывать при советах и подтверждении): {note}")

    stmt = (
        select(Order)
        .where(
            Order.user_id == user.id,
            Order.status != OrderStatus.CANCELLED.value,
        )
        .order_by(desc(Order.created_at))
        .limit(3)
    )
    result = await db.execute(stmt)
    orders = list(result.scalars().all())

    if orders:
        history_lines: list[str] = []
        for o in orders:
            dt = o.created_at
            if isinstance(dt, datetime):
                d_s = dt.strftime("%d.%m")
            else:
                d_s = "—"
            raw = o.items_json if isinstance(o.items_json, dict) else {}
            items_list = raw.get("items")
            names: list[str] = []
            if isinstance(items_list, list):
                for it in items_list:
                    if isinstance(it, dict) and it.get("name"):
                        names.append(str(it["name"]).strip())
            if not names:
                names = ["(состав не сохранён)"]
            st = (o.status or "").strip()
            history_lines.append(f"- {d_s} [{st}]: {', '.join(names[:12])}")

        parts.append("ПОСЛЕДНИЕ ЗАКАЗЫ ГОСТЯ:\n" + "\n".join(history_lines))

    if not parts:
        return "Гость новый или записей о предпочтениях нет — общайся нейтрально-доброжелательно."

    return "\n\n".join(parts)
