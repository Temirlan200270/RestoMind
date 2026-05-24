"""
Тест бота из админки без WhatsApp (E0.1).
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request

from app.db.session import async_session_factory, redis_client
from app.services.ai_brain import call_openai
from app.services.context_engine import (
    build_llm_prompt_bundle,
    fetch_ai_read_context,
    schedule_save_ai_context_snapshot,
)
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
from app.services.intent_router import route_intent

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

    read_ctx = await fetch_ai_read_context(phone, org_id)
    bundle = await build_llm_prompt_bundle(
        read_ctx,
        organization_id=org_id,
        message_text=message_text,
    )
    schedule_save_ai_context_snapshot(
        phone,
        org_id,
        read_ctx,
        menu_context_text=bundle.menu_context,
    )

    from app.services.ai_usage import schedule_log_ai_usage
    ai_response = await call_openai(
        history,
        message_text,
        bundle.menu_context,
        bundle.kb_context,
        draft_order_context=bundle.draft_ctx,
        sales_strategy_context=bundle.strategy_ctx,
        customer_context=bundle.customer_ctx,
        current_time_context=bundle.current_time_ctx,
        raise_on_transient=False,
    )

    schedule_log_ai_usage(org_id, getattr(ai_response, "_usage", None))

    inbound_mid = f"admin-test-bot:{secrets.token_hex(8)}"
    async with async_session_factory() as db:
        result = await route_intent(
            db,
            phone,
            ai_response,
            menu_items=bundle.menu_items,
            organization_id=org_id,
            inbound_message_id=inbound_mid,
            sales_gastro_hint=bundle.sales_gastro_hint,
            sales_target_iiko_ids=bundle.sales_target_iiko_ids,
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
