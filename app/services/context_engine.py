"""
Параллельная фаза чтения для AI-контекста: независимые AsyncSession на каждый запрос.

Один и тот же session нельзя безопасно нагружать concurrent awaits — поэтому asyncio.gather
здесь используется с отдельными короткими сессиями (пул async_engine).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import MenuItem, Order, Organization, User
from app.db.session import async_session_factory
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


async def fetch_ai_read_context(phone: str, organization_id: int) -> AIReadContext:
    """
    Собирает независимые части контекста параллельно; «user» и customer_ctx — в одной сессии,
    т.к. build_customer_context читает заказы по user_id.
    """

    phone_s = (phone or "").strip()

    async def get_menu() -> list[MenuItem]:
        async with async_session_factory() as db:
            return await load_available_menu(db, organization_id=organization_id)

    async def get_user_and_customer_ctx() -> tuple[User | None, str]:
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
            return u, ctx

    async def get_org() -> Organization | None:
        async with async_session_factory() as db:
            return await db.get(Organization, organization_id)

    async def get_kb() -> str:
        async with async_session_factory() as db:
            return await load_knowledge_context_block(db, organization_id)

    async def get_draft() -> Order | None:
        async with async_session_factory() as db:
            return await get_open_draft_order(db, phone_s, organization_id)

    menu_items, u_ctx, org_row, kb, draft = await asyncio.gather(
        get_menu(),
        get_user_and_customer_ctx(),
        get_org(),
        get_kb(),
        get_draft(),
    )
    u_row, customer_ctx = u_ctx
    return AIReadContext(
        menu_items=menu_items,
        user=u_row,
        org=org_row,
        kb_context=kb,
        draft_row=draft,
        customer_ctx=customer_ctx,
    )
