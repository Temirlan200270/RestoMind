"""Helpers for lightweight trace and conversation context."""

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
