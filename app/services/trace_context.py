"""Helpers for lightweight trace and conversation context.

Helpers publish_order_event / publish_chat_event / publish_human_event /
publish_state_event обеспечивают единый набор полей для всех WebSocket-событий
независимо от источника (webhook или admin API). Поля trace_id и conversation_id
всегда присутствуют в payload (None когда контекст недоступен — например,
в admin-initiated событиях без входящего сообщения от клиента).

Phase 2 Control Plane: ``contextvars`` для неявной propagation в emit_event / LLM / iiko.
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from typing import Any, Iterator


def build_conversation_id(organization_id: int, phone: str) -> str:
    return f"org:{int(organization_id)}:phone:{(phone or '').strip()}"


def build_trace_id(seed: str | None = None) -> str:
    raw = (seed or "").strip()
    if raw:
        return raw[:120]
    return uuid.uuid4().hex


_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "restomind_trace_id",
    default=None,
)
_conversation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "restomind_conversation_id",
    default=None,
)


def get_trace_id() -> str | None:
    return _trace_id_var.get()


def get_conversation_id() -> str | None:
    return _conversation_id_var.get()


def trace_log_prefix() -> str:
    """Structured prefix for Render logs: ``[trace_id=...] ``."""
    tid = get_trace_id()
    return f"[trace_id={tid}] " if tid else ""


def activate_trace_context(
    *,
    trace_id: str,
    conversation_id: str,
) -> tuple[contextvars.Token[str | None], contextvars.Token[str | None]]:
    return (
        _trace_id_var.set(trace_id),
        _conversation_id_var.set(conversation_id),
    )


def reset_trace_context(
    trace_token: contextvars.Token[str | None],
    conversation_token: contextvars.Token[str | None],
) -> None:
    _trace_id_var.reset(trace_token)
    _conversation_id_var.reset(conversation_token)


@contextmanager
def trace_context(trace_id: str, conversation_id: str) -> Iterator[None]:
    tokens = activate_trace_context(trace_id=trace_id, conversation_id=conversation_id)
    try:
        yield
    finally:
        reset_trace_context(*tokens)


def enrich_payload_with_trace(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Merge active trace context into payload when keys are absent."""
    out = dict(payload or {})
    tid = get_trace_id()
    cid = get_conversation_id()
    if tid and "trace_id" not in out:
        out["trace_id"] = tid
    if cid and "conversation_id" not in out:
        out["conversation_id"] = cid
    return out


def stamp_order_meta_trace(
    items_json: dict[str, Any] | None,
    *,
    trace_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    """Persist trace on order draft (items_json.order_meta) for timeline joins."""
    out = dict(items_json or {})
    meta = out.get("order_meta")
    meta_d = dict(meta) if isinstance(meta, dict) else {}
    if trace_id:
        meta_d["trace_id"] = trace_id
    if conversation_id:
        meta_d["conversation_id"] = conversation_id
    out["order_meta"] = meta_d
    return out


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
