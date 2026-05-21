"""Control Plane Phase 2: trace_id propagation."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import SystemEvent
from app.services.system_events import BusinessEvent, emit_event
from app.services.trace_context import (
    build_trace_id,
    enrich_payload_with_trace,
    get_conversation_id,
    get_trace_id,
    stamp_order_meta_trace,
    trace_context,
)


def test_build_trace_id_uses_whatsapp_message_id_seed() -> None:
    wmid = "wamid.HBgLNzcyMDExMjIzMzYVAgASGBQzQTRCMEU5Q0Y3RjA4Q0Y3RjA4AA=="
    assert build_trace_id(wmid) == wmid[:120]
    assert len(build_trace_id(None)) == 32


def test_trace_context_contextvars() -> None:
    assert get_trace_id() is None
    assert get_conversation_id() is None
    with trace_context("trace-abc", "conv-xyz"):
        assert get_trace_id() == "trace-abc"
        assert get_conversation_id() == "conv-xyz"
    assert get_trace_id() is None
    assert get_conversation_id() is None


def test_enrich_payload_with_trace_preserves_explicit_values() -> None:
    with trace_context("ctx-trace", "ctx-conv"):
        out = enrich_payload_with_trace({"trace_id": "explicit", "foo": 1})
    assert out["trace_id"] == "explicit"
    assert out["conversation_id"] == "ctx-conv"
    assert out["foo"] == 1


def test_stamp_order_meta_trace() -> None:
    stamped = stamp_order_meta_trace(
        {"items": [], "order_meta": {"payment_method": "cash"}},
        trace_id="t1",
        conversation_id="c1",
    )
    meta = stamped["order_meta"]
    assert meta["trace_id"] == "t1"
    assert meta["conversation_id"] == "c1"
    assert meta["payment_method"] == "cash"


@pytest.mark.asyncio
async def test_emit_event_injects_trace_from_context(db_session: AsyncSession) -> None:
    with trace_context("trace-emit-1", "conv-emit-1"):
        result = await emit_event(
            db_session,
            BusinessEvent(
                org_id=1,
                type="ai.response.generated",
                actor="ai",
                payload={"intent": "chat"},
            ),
        )
    assert result is not None
    row = await db_session.get(SystemEvent, result.id)
    assert row is not None
    payload = row.payload_json or {}
    assert payload.get("trace_id") == "trace-emit-1"
    assert payload.get("conversation_id") == "conv-emit-1"
    assert payload.get("intent") == "chat"


@pytest.mark.asyncio
async def test_emit_event_stores_causal_chain_fields(db_session: AsyncSession) -> None:
    from app.db.models import Organization

    org = Organization(name="Causal Org", slug="causal-org")
    db_session.add(org)
    await db_session.flush()
    parent_id = "parent-event-uuid"
    result = await emit_event(
        db_session,
        BusinessEvent(
            org_id=int(org.id),
            type="order.confirmed",
            actor="system",
            payload={"order_id": 42},
            parent_event_id=parent_id,
            caused_by="payment.completed",
        ),
    )
    assert result is not None
    row = await db_session.get(SystemEvent, result.id)
    payload = row.payload_json or {}
    assert payload.get("parent_event_id") == parent_id
    assert payload.get("caused_by") == "payment.completed"


@pytest.mark.asyncio
async def test_build_trace_timeline_merges_events_and_chat(db_session: AsyncSession) -> None:
    from app.db.models import ChatLog, Organization, User
    from app.services.trace_timeline import build_trace_timeline

    org = Organization(name="Trace TL Org", slug="trace-tl-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005550101")
    db_session.add(user)
    await db_session.flush()
    tid = "trace-timeline-test-001"
    with trace_context(tid, "conv-1"):
        await emit_event(
            db_session,
            BusinessEvent(org_id=int(org.id), type="ai.response.generated", actor="ai", payload={"intent": "chat"}),
        )
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="user",
            content="Привет",
            meta_json={"trace_id": tid, "conversation_id": "conv-1"},
        ),
    )
    await db_session.flush()

    out = await build_trace_timeline(db_session, org_id=int(org.id), trace_id=tid)
    assert out["total"] >= 2
    kinds = {entry["kind"] for entry in out["entries"]}
    assert "system_event" in kinds
    assert "chat_log" in kinds


@pytest.mark.asyncio
async def test_process_with_retry_forwards_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import webhooks
    from app.db.models import Base, Organization

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as db:
        org = Organization(name="WA Active", is_active=True)
        db.add(org)
        await db.commit()
        org_id = int(org.id)

    captured: dict[str, str | None] = {}

    async def fake_process_message(*_args, **kwargs):
        captured["trace_id"] = kwargs.get("trace_id")

    async def fake_try_start(_db, **_kw):
        return True

    monkeypatch.setattr(webhooks, "async_session_factory", session_factory)
    monkeypatch.setattr(webhooks, "process_message", fake_process_message)
    monkeypatch.setattr(webhooks, "try_start_whatsapp_inbound_in_db", fake_try_start)
    monkeypatch.setattr(webhooks, "mark_whatsapp_inbound_done", lambda *_a, **_k: None)
    monkeypatch.setattr(webhooks, "cache_whatsapp_inbound_done_redis", lambda *_a, **_k: None)

    await webhooks.process_with_retry(
        "+77001112233",
        "hello",
        whatsapp_message_id="wamid.test123",
        organization_id=org_id,
        trace_id="trace-from-arq",
    )
    assert captured.get("trace_id") == "trace-from-arq"
    await engine.dispose()


@pytest.mark.asyncio
async def test_order_created_event_has_trace_from_route(db_with_menu: AsyncSession) -> None:
    from app.schemas.ai_schemas import AIBrainResponse, OrderItem
    from app.services.intent_router import route_intent

    ai = AIBrainResponse(
        intent="order",
        reply_text="Ок",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="pickup",
        payment_method="cash",
    )
    with trace_context("trace-order-1", "conv-order-1"):
        await route_intent(
            db_with_menu,
            "+77001234567",
            ai,
            menu_items=None,
            organization_id=1,
            trace_id="trace-order-1",
            conversation_id="conv-order-1",
        )
    events = (
        await db_with_menu.execute(
            select(SystemEvent).where(SystemEvent.event_type == "order.created"),
        )
    ).scalars().all()
    assert events
    assert all((ev.payload_json or {}).get("trace_id") == "trace-order-1" for ev in events)
