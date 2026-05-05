"""
Тест бота из админки без WhatsApp (E0.1).
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.db.models import Organization, User
from app.db.session import async_session_factory, redis_client
from app.services.ai_brain import call_openai
from app.services.customer_context import build_customer_context
from app.services.dialog_mgr import (
    UserState,
    append_to_history,
    get_chat_history,
    get_user_state,
    set_pending_booking,
    set_pending_order,
    set_user_state,
    update_user_session_fields_in_db,
)
from app.services.events import publish_event
from app.services.intent_router import get_open_draft_order, route_intent
from app.services.knowledge_context import load_knowledge_context_block
from app.services.order_logic import (
    build_menu_context_for_ai,
    format_draft_order_context_for_prompt,
    load_available_menu,
)
from app.services.sales_strategy import build_sales_strategy, format_strategy_for_prompt
from app.services.time_context import format_org_current_time_block

from .deps import admin_org_from_session, require_admin_session_active
from .schemas import TextRequest

test_bot_router = APIRouter(
    prefix="/admin",
    tags=["Admin Panel"],
    dependencies=[Depends(require_admin_session_active)],
)


@test_bot_router.post("/test-bot")
async def test_bot(request: Request, body: TextRequest) -> dict:
    """
    Тестовый endpoint: эмулирует диалог с ботом без WhatsApp.
    Использует фиктивный номер 'test-admin', проходит полный цикл AI.
    """
    from app.api.webhooks import (
        handle_booking_confirmation,
        handle_confirmation,
        handle_order_payment_choice,
    )

    org_id = admin_org_from_session(request)
    phone = "test-admin"
    message_text = body.text

    state = await get_user_state(redis_client, phone, organization_id=org_id)

    if state == UserState.HUMAN_MODE:
        return {"reply": "[HUMAN_MODE — AI отключён]", "state": state.value, "intent": None}

    if state == UserState.AWAITING_ORDER_PAYMENT:
        reply = await handle_order_payment_choice(phone, message_text, org_id)
        await append_to_history(redis_client, phone, "user", message_text, organization_id=org_id)
        await append_to_history(redis_client, phone, "assistant", reply or "", organization_id=org_id)
        new_state = await get_user_state(redis_client, phone, organization_id=org_id)
        return {"reply": reply, "state": new_state.value, "intent": None}

    if state == UserState.CONFIRMING_ORDER:
        reply = await handle_confirmation(phone, message_text, org_id)
        await append_to_history(redis_client, phone, "user", message_text, organization_id=org_id)
        await append_to_history(redis_client, phone, "assistant", reply or "", organization_id=org_id)
        new_state = await get_user_state(redis_client, phone, organization_id=org_id)
        return {"reply": reply, "state": new_state.value, "intent": None}

    if state == UserState.CONFIRMING_BOOKING:
        reply = await handle_booking_confirmation(phone, message_text, org_id)
        await append_to_history(redis_client, phone, "user", message_text, organization_id=org_id)
        await append_to_history(redis_client, phone, "assistant", reply, organization_id=org_id)
        new_state = await get_user_state(redis_client, phone, organization_id=org_id)
        return {"reply": reply, "state": new_state.value, "intent": None}

    history = await get_chat_history(redis_client, phone, organization_id=org_id)
    await append_to_history(redis_client, phone, "user", message_text, organization_id=org_id)

    async with async_session_factory() as db:
        org_ent = await db.get(Organization, org_id)
        current_time_ctx = format_org_current_time_block(
            getattr(org_ent, "timezone", None) if org_ent is not None else "Asia/Almaty",
            getattr(org_ent, "schedule_json", None) if org_ent is not None else None,
        )
        menu_items = await load_available_menu(db, organization_id=org_id)
        menu_context = await build_menu_context_for_ai(menu_items, message_text)
        u_row = await db.scalar(
            select(User).where(User.phone == phone, User.organization_id == org_id),
        )
        customer_ctx = await build_customer_context(db, u_row)
        kb_context = await load_knowledge_context_block(db, org_id)
        draft_row = await get_open_draft_order(db, phone, org_id)
        draft_ctx = format_draft_order_context_for_prompt(
            draft_row.items_json if draft_row else None,
        )
        strategy_ctx = ""
        sales_gastro_hint = ""
        sales_target_iiko_ids: list[str] = []
        if draft_row and isinstance(draft_row.items_json, dict):
            cart = [
                x for x in (draft_row.items_json.get("items") or [])
                if isinstance(x, dict)
            ]
            om = draft_row.items_json.get("order_meta")
            meta_d = om if isinstance(om, dict) else {}
            total = float(draft_row.total_price or 0)
            decision = build_sales_strategy(
                cart, total, meta_d, menu_items,
                u_row.meta_json if u_row is not None else None,
            )
            strategy_ctx = format_strategy_for_prompt(decision)
            sales_gastro_hint = (decision.gastro_hint or "").strip()
            sales_target_iiko_ids = list(decision.target_iiko_ids or [])
        ai_response = await call_openai(
            history,
            message_text,
            menu_context,
            kb_context,
            draft_order_context=draft_ctx,
            sales_strategy_context=strategy_ctx,
            customer_context=customer_ctx,
            current_time_context=current_time_ctx,
            raise_on_transient=False,
        )
        inbound_mid = f"admin-test-bot:{secrets.token_hex(8)}"
        result = await route_intent(
            db,
            phone,
            ai_response,
            menu_items=menu_items,
            organization_id=org_id,
            inbound_message_id=inbound_mid,
            sales_gastro_hint=sales_gastro_hint,
            sales_target_iiko_ids=sales_target_iiko_ids,
        )
        await update_user_session_fields_in_db(
            db,
            phone=phone,
            organization_id=org_id,
            current_state=(result.new_state.value if result.new_state else None),
            **(
                {"current_pending_order_id": result.pending_order_id}
                if result.pending_order_id is not None
                else {}
            ),
            **(
                {"current_pending_booking_id": result.pending_booking_id}
                if result.pending_booking_id is not None
                else {}
            ),
        )

        await db.commit()

        if result.new_state:
            await set_user_state(redis_client, phone, result.new_state, organization_id=org_id)
        if result.pending_order_id:
            await set_pending_order(redis_client, phone, result.pending_order_id, organization_id=org_id)
        if result.pending_booking_id:
            await set_pending_booking(redis_client, phone, result.pending_booking_id, organization_id=org_id)
        for evt_type, evt_data in (result.events or []):
            await publish_event(evt_type, evt_data)

    await append_to_history(redis_client, phone, "assistant", result.reply_text, organization_id=org_id)

    new_state = await get_user_state(redis_client, phone, organization_id=org_id)
    return {
        "reply": result.reply_text,
        "state": new_state.value,
        "intent": ai_response.intent,
        "items": [item.model_dump() for item in ai_response.items] if ai_response.items else [],
    }
