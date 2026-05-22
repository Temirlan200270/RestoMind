"""
Параллельная фаза чтения для AI-контекста: независимые AsyncSession на каждый запрос.

Один и тот же session нельзя безопасно нагружать concurrent awaits — поэтому asyncio.gather
здесь используется с отдельными короткими сессиями (пул async_engine).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from datetime import timedelta, timezone

from sqlalchemy import select

from app.db.models import AIContextSnapshot, MenuItem, Order, Organization, SystemEvent, Tenant, User
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)
from app.services.customer_context import build_customer_context
from app.services.intent_router import get_open_draft_order
from app.services.knowledge_context import load_knowledge_context_block
from app.services.order_logic import load_available_menu


@dataclass(frozen=True, slots=True)
class AIReadContext:
    menu_items: list[MenuItem]
    user: User | None
    org: Organization | None
    kb_context: str
    draft_row: Order | None
    customer_ctx: str
    user_preferences: dict
    tenant: Tenant | None = None


async def fetch_ai_read_context(phone: str, organization_id: int) -> AIReadContext:
    """
    Собирает независимые части контекста параллельно; «user» и customer_ctx — в одной сессии,
    т.к. build_customer_context читает заказы по user_id.
    """

    phone_s = (phone or "").strip()

    async def get_menu() -> list[MenuItem]:
        async with async_session_factory() as db:
            # include_unavailable=True: ИИ видит стоп-позиции (с меткой), но не добавляет их в заказ
            return await load_available_menu(db, organization_id=organization_id, include_unavailable=True)

    async def get_user_and_customer_ctx() -> tuple[User | None, str, dict]:
        async with async_session_factory() as db:
            u = await db.scalar(
                select(User).where(
                    User.phone == phone_s,
                    User.organization_id == organization_id,
                ),
            )
            ctx = await build_customer_context(db, u)
            if u is not None:
                try:
                    from app.services.loyalty import get_loyalty_context_line
                    loyalty_line = await get_loyalty_context_line(db, organization_id, u.id)
                    if loyalty_line:
                        ctx = f"{ctx}\n{loyalty_line}".strip() if ctx else loyalty_line
                except Exception:
                    pass
            prefs: dict = {}
            if u is not None:
                try:
                    from app.services.personalization import get_user_preferences
                    prefs = await get_user_preferences(db, u.id, organization_id)
                except Exception:
                    pass
            return u, ctx, prefs

    async def get_org() -> Organization | None:
        async with async_session_factory() as db:
            return await db.get(Organization, organization_id)

    async def get_tenant() -> Tenant | None:
        async with async_session_factory() as db:
            row = await db.scalar(
                select(Tenant)
                .join(Organization, Organization.tenant_id == Tenant.id)
                .where(Organization.id == organization_id)
            )
            return row

    async def get_kb() -> str:
        async with async_session_factory() as db:
            return await load_knowledge_context_block(db, organization_id)

    async def get_draft() -> Order | None:
        async with async_session_factory() as db:
            return await get_open_draft_order(db, phone_s, organization_id)

    menu_items, u_ctx, org_row, kb, draft, tenant_row = await asyncio.gather(
        get_menu(),
        get_user_and_customer_ctx(),
        get_org(),
        get_kb(),
        get_draft(),
        get_tenant(),
    )
    u_row, customer_ctx, user_preferences = u_ctx
    return AIReadContext(
        menu_items=menu_items,
        user=u_row,
        org=org_row,
        kb_context=kb,
        draft_row=draft,
        customer_ctx=customer_ctx,
        user_preferences=user_preferences,
        tenant=tenant_row,
    )


def schedule_save_ai_context_snapshot(
    phone: str,
    organization_id: int,
    context: AIReadContext,
    *,
    menu_context_text: str | None = None,
    trace_id: str | None = None,
    conversation_id: str | None = None,
) -> str:
    """Fire-and-forget: сохраняет снимок в фоне. Возвращает snapshot_id сразу (не ждёт БД)."""
    snapshot_id = str(uuid.uuid4())
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            save_ai_context_snapshot(
                phone,
                organization_id,
                context,
                menu_context_text=menu_context_text,
                trace_id=trace_id,
                conversation_id=conversation_id,
                snapshot_id=snapshot_id,
            ),
            name=f"ai_ctx_snapshot_{organization_id}",
        )
    except RuntimeError:
        pass
    return snapshot_id


async def save_ai_context_snapshot(
    phone: str,
    organization_id: int,
    context: AIReadContext,
    *,
    menu_context_text: str | None = None,
    trace_id: str | None = None,
    conversation_id: str | None = None,
    snapshot_id: str | None = None,
) -> str:
    """Сохраняет снимок AI-контекста перед LLM-вызовом. Возвращает snapshot_id (UUID).

    Открывает собственную сессию и коммитит — не зависит от открытых транзакций.
    Ошибки не пробрасывает: snapshot — аудит, не критичный path.
    """
    snapshot_id = snapshot_id or str(uuid.uuid4())
    from app.services.trace_context import get_conversation_id, get_trace_id

    effective_trace_id = trace_id or get_trace_id()
    effective_conversation_id = conversation_id or get_conversation_id()
    try:
        # Минимальный снимок цен: только audit-поля нужные для query «цена X в момент T»
        # iiko_id — стабильный идентификатор (name может меняться при переименовании)
        # price + is_available — единственные поля, которые меняются и влияют на решение
        menu_prices_snapshot = [
            {
                "iiko_id": m.iiko_id or "",
                "price": float(m.price or 0),
                "is_available": bool(m.is_available),
            }
            for m in context.menu_items
        ]
        business_state = {
            "org_id": organization_id,
            "org_name": context.org.name if context.org else None,
            "org_timezone": getattr(context.org, "timezone", None) if context.org else None,
            "menu_items_count": len(context.menu_items),
            "stoplist_count": sum(1 for m in context.menu_items if not m.is_available),
            # Structured audit: iiko_id + price + availability at decision time
            "menu_prices_snapshot": menu_prices_snapshot,
            # Frozen menu text passed to LLM — используется в replay для точного воспроизведения
            "menu_context_text": menu_context_text,
            "trace_id": effective_trace_id,
            "conversation_id": effective_conversation_id,
        }
        async with async_session_factory() as db:
            chat_history_slice = await _load_chat_history_slice(
                db, organization_id, phone, limit=20,
            )
            customer_state = {
                "has_draft": context.draft_row is not None,
                "draft_id": context.draft_row.id if context.draft_row else None,
                "draft_total": float(context.draft_row.total_price or 0) if context.draft_row else None,
                "customer_ctx_snippet": (context.customer_ctx or "")[:500],
                "user_preferences": context.user_preferences,
                "chat_history_slice": chat_history_slice,
            }
            event_slice = await _load_recent_event_slice(db, organization_id, minutes=15)
            snap = AIContextSnapshot(
                id=snapshot_id,
                organization_id=organization_id,
                phone=phone,
                business_state=business_state,
                customer_state=customer_state,
                event_slice=event_slice,
            )
            db.add(snap)
            await db.commit()
    except Exception:
        logger.exception("save_ai_context_snapshot failed for org=%d phone=%s", organization_id, phone)
    return snapshot_id


async def _load_chat_history_slice(
    db,
    organization_id: int,
    phone: str,
    *,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Последние сообщения диалога из ChatLog для replay (хронологический порядок)."""
    from app.db.models import ChatLog, User

    user = await db.scalar(
        select(User).where(
            User.organization_id == int(organization_id),
            User.phone == phone,
        ).limit(1)
    )
    if user is None:
        return []
    rows = (
        await db.execute(
            select(ChatLog.role, ChatLog.content)
            .where(ChatLog.user_id == int(user.id))
            .order_by(desc(ChatLog.id))
            .limit(limit)
        )
    ).all()
    out: list[dict[str, str]] = []
    for role, content in reversed(rows):
        r = (role or "").strip().lower()
        if r not in ("user", "assistant", "operator"):
            continue
        text = (content or "").strip()
        if not text:
            continue
        out.append({"role": "assistant" if r == "operator" else r, "content": text})
    return out


def build_menu_context_from_prices_snapshot(
    menu_prices_snapshot: list[dict[str, object]] | None,
) -> str | None:
    """Восстанавливает текст меню для replay, если menu_context_text не сохранён (G3 edge-case)."""
    if not menu_prices_snapshot or not isinstance(menu_prices_snapshot, list):
        return None
    lines: list[str] = []
    for row in menu_prices_snapshot:
        if not isinstance(row, dict):
            continue
        iiko_id = str(row.get("iiko_id") or "").strip()
        try:
            price = float(row.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        available = bool(row.get("is_available", True))
        label = iiko_id or "item"
        stop_tag = "" if available else " [СТОП — временно недоступно]"
        lines.append(f"- {label}: {price:.0f} ₸ [id: {iiko_id}]{stop_tag}" if iiko_id else f"- {label}: {price:.0f} ₸{stop_tag}")
    if not lines:
        return None
    return "\n".join(lines)


async def _load_recent_event_slice(
    db,
    organization_id: int,
    minutes: int = 15,
    limit: int = 20,
) -> dict:
    """Phase 3.2: загружает последние N SystemEvent за minutes минут до вызова LLM.

    Даёт AI Context Snapshot "живую" картину ресторана:
    что происходило (заказы, эскалации, платежи) прямо перед этим решением.
    Без event_slice невозможно объяснить совет ИИ на 100%.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)

    try:
        from sqlalchemy import select
        from app.db.models import SystemEvent
        rows = (await db.execute(
            select(
                SystemEvent.id,
                SystemEvent.event_type,
                SystemEvent.source,
                SystemEvent.entity_type,
                SystemEvent.entity_id,
                SystemEvent.created_at,
                SystemEvent.payload_json,
            )
            .where(
                SystemEvent.organization_id == organization_id,
                SystemEvent.created_at >= cutoff,
            )
            .order_by(SystemEvent.created_at.desc())
            .limit(limit)
        )).mappings().all()

        events = [
            {
                "id": r["id"],
                "type": r["event_type"],
                "source": r["source"],
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "ts": r["created_at"].isoformat() if r["created_at"] else None,
                "payload": {
                    k: v for k, v in (r["payload_json"] or {}).items()
                    if not k.startswith("_")  # убираем служебные _actor, _version
                },
            }
            for r in reversed(rows)  # хронологический порядок
        ]
        return {
            "window_minutes": minutes,
            "cutoff_utc": cutoff.isoformat(),
            "events_count": len(events),
            "events": events,
        }
    except Exception as exc:
        logger.exception("_load_recent_event_slice failed org=%d: %s", organization_id, exc)
        return {"window_minutes": minutes, "events_count": 0, "events": [], "error": str(exc)}
