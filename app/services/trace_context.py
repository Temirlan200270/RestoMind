"""Helpers for lightweight trace and conversation context.

Helpers publish_order_event / publish_chat_event / publish_human_event /
publish_state_event обеспечивают единый набор полей для всех WebSocket-событий
независимо от источника (webhook или admin API). Поля trace_id и conversation_id
всегда присутствуют в payload (None когда контекст недоступен — например,
в admin-initiated событиях без входящего сообщения от клиента).
"""

from __future__ import annotations

import uuid
from typing import Any


def build_conversation_id(organization_id: int, phone: str) -> str:
    return f"org:{int(organization_id)}:phone:{(phone or '').strip()}"


def build_trace_id(seed: str | None = None) -> str:
    raw = (seed or "").strip()
    if raw:
        return raw[:120]
    return uuid.uuid4().hex


def trace_payload(
    *,
    trace_id: str,
    conversation_id: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "trace_id": trace_id,
        "conversation_id": conversation_id,
    }
    if extra:
        payload.update(extra)
    return payload


def merge_trace_meta(
    meta: dict[str, Any] | None,
    *,
    trace_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    out = dict(meta or {})
    out["trace_id"] = trace_id
    out["conversation_id"] = conversation_id
    return out


# ─── Typed publish helpers ────────────────────────────────────────────────────
#
# Каждый helper задаёт каноническую форму payload-а для своего event_type.
# Поля trace_id / conversation_id всегда присутствуют (могут быть None).
# **extra позволяет дополнить payload event-специфичными полями без потери
# типизированного ядра.


async def publish_order_event(
    event_type: str,
    *,
    order_id: int,
    organization_id: int,
    phone: str = "",
    status: str = "",
    total_price: float | None = None,
    iiko_last_error: str | None = None,
    trace_id: str | None = None,
    conversation_id: str | None = None,
    **extra: Any,
) -> None:
    """Публикует событие заказа (order_updated, order_deleted, …) с трейс-полями."""
    from app.services.events import publish_event

    payload: dict[str, Any] = {
        "order_id": order_id,
        "organization_id": organization_id,
        "phone": phone or "",
        "trace_id": trace_id or None,
        "conversation_id": conversation_id or None,
    }
    if status:
        payload["status"] = status
    if total_price is not None:
        payload["total_price"] = total_price
    if iiko_last_error is not None:
        payload["iiko_last_error"] = iiko_last_error
    payload.update(extra)
    await publish_event(event_type, payload)


async def publish_chat_event(
    *,
    phone: str,
    role: str,
    content: str,
    organization_id: int,
    chat_log_id: int | None = None,
    delivery_status: str = "sending",
    trace_id: str | None = None,
    conversation_id: str | None = None,
    **extra: Any,
) -> None:
    """Публикует событие нового сообщения (new_message) с трейс-полями."""
    from app.services.events import publish_event

    payload: dict[str, Any] = {
        "phone": phone,
        "role": role,
        "content": content,
        "organization_id": organization_id,
        "delivery_status": delivery_status,
        "trace_id": trace_id or None,
        "conversation_id": conversation_id or None,
    }
    if chat_log_id is not None:
        payload["id"] = chat_log_id
    payload.update(extra)
    await publish_event("new_message", payload)


async def publish_human_event(
    *,
    phone: str,
    organization_id: int,
    reason: str = "",
    user_message: str = "",
    trace_id: str | None = None,
    conversation_id: str | None = None,
    **extra: Any,
) -> None:
    """Публикует событие эскалации (human_needed) с трейс-полями."""
    from app.services.events import publish_event

    payload: dict[str, Any] = {
        "phone": phone,
        "organization_id": organization_id,
        "reason": reason,
        "user_message": (user_message or "")[:500],
        "trace_id": trace_id or None,
        "conversation_id": conversation_id or None,
    }
    payload.update(extra)
    await publish_event("human_needed", payload)


async def publish_state_event(
    *,
    phone: str,
    state: str,
    organization_id: int,
    trace_id: str | None = None,
    conversation_id: str | None = None,
) -> None:
    """Публикует событие смены состояния диалога (state_changed) с трейс-полями."""
    from app.services.events import publish_event

    await publish_event("state_changed", {
        "phone": phone,
        "state": state,
        "organization_id": organization_id,
        "trace_id": trace_id or None,
        "conversation_id": conversation_id or None,
    })
