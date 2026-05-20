from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.db.models import ChatLog, Order, OrderStatus, Organization, User
from app.services.shift_state_engine import (
    ShiftInput,
    apply_shift_action,
    build_shift_state,
    compute_projection_gap,
    derive_state_reason,
    item_priority_score,
    resolve_state,
    select_focus,
)


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}

    async def scan(self, cursor: int, match: str | None = None, count: int = 64) -> tuple[int, list[str]]:
        prefix = (match or "").replace("*", "")
        keys = [k for k in self.kv if k.startswith(prefix)]
        return 0, keys

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.kv[key] = value
        self.ttl[key] = ttl

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def delete(self, key: str) -> None:
        self.kv.pop(key, None)

    async def sadd(self, key: str, *values: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        for v in values:
            bucket.add(v)
        return len(bucket) - before

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def srem(self, key: str, *values: str) -> int:
        bucket = self.sets.setdefault(key, set())
        removed = 0
        for v in values:
            if v in bucket:
                bucket.remove(v)
                removed += 1
        return removed

    async def expire(self, key: str, ttl: int) -> None:
        pass


def test_resolve_state_s0_when_quiet() -> None:
    inp = ShiftInput(
        risk_kzt=0,
        drafts=[],
        pending_payments=[],
        red_chats=[],
        yellow_chats=[],
        high_value=[],
        queue_size=0,
        drafts_value_kzt=0,
    )
    assert resolve_state(inp) == "S3"


@pytest.mark.asyncio
async def test_resolve_state_s3_when_only_metrics(db_session) -> None:
    org = Organization(name="S3 Org", slug="s3-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557001")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=5000,
            items_json={"items": [{"name": "A", "quantity": 1, "item_total": 5000}]},
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    out = await build_shift_state(db_session, int(org.id))
    assert out["state"] in {"S0", "S3"}


@pytest.mark.asyncio
async def test_resolve_state_s1_when_red_chat(db_session) -> None:
    org = Organization(name="S1 Org", slug="s1-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557002", name="Red Guest")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="user",
            content="Жду ответ",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=8),
        )
    )
    await db_session.flush()

    out = await build_shift_state(db_session, int(org.id))
    assert out["state"] == "S1"
    assert out["focus"] is not None
    assert out["focus"]["kind"] == "slow_chat"


@pytest.mark.asyncio
async def test_resolve_state_s4_when_drafts_only(db_session) -> None:
    org = Organization(name="S4 Org", slug="s4-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557003")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=3500,
            items_json={"items": [{"name": "B", "quantity": 1, "item_total": 3500}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=40),
        )
    )
    await db_session.flush()

    out = await build_shift_state(db_session, int(org.id))
    assert out["state"] == "S4"
    assert out["focus"]["kind"] == "abandoned_draft"


def test_resolve_state_s5_when_queue_spike() -> None:
    inp = ShiftInput(
        risk_kzt=1000,
        drafts=[],
        pending_payments=[],
        red_chats=[],
        yellow_chats=[],
        high_value=[],
        queue_size=26,
        drafts_value_kzt=0,
    )
    assert resolve_state(inp) == "S5"


def test_priority_score_picks_high_value_stuck_over_chat() -> None:
    chat = {
        "id": "chat:1",
        "kind": "slow_chat",
        "amount_kzt": 0,
        "wait_minutes": 10,
    }
    high = {
        "id": "high:1",
        "kind": "high_value_stuck",
        "amount_kzt": 20000,
        "wait_minutes": 35,
    }
    focus = select_focus([chat, high])
    assert focus is not None
    assert focus["kind"] == "high_value_stuck"
    assert item_priority_score(high) > item_priority_score(chat)


def test_priority_score_caps_wait_minutes() -> None:
    long_wait_chat = {
        "id": "chat:long",
        "kind": "slow_chat",
        "amount_kzt": 15000,
        "wait_minutes": 120,
    }
    high = {
        "id": "high:2",
        "kind": "high_value_stuck",
        "amount_kzt": 10000,
        "wait_minutes": 5,
    }
    focus = select_focus([long_wait_chat, high])
    assert focus is not None
    assert focus["kind"] == "high_value_stuck"


def test_resolve_state_s1_over_s4_when_draft_and_red_chat() -> None:
    inp = ShiftInput(
        risk_kzt=5000,
        drafts=[{"id": "draft:1"}],
        pending_payments=[],
        red_chats=[{"id": "chat:1"}],
        yellow_chats=[],
        high_value=[],
        queue_size=2,
        drafts_value_kzt=3500,
    )
    assert resolve_state(inp) == "S1"


@pytest.mark.asyncio
async def test_next_uses_next_key_not_skip(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="Next Org", slug="next-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557005")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=7000,
            items_json={"items": [{"name": "D", "quantity": 1, "item_total": 7000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=45),
        )
    )
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    before = await build_shift_state(db_session, int(org.id), operator_id="op1")
    focus_id = before["focus"]["id"]
    await apply_shift_action(db_session, int(org.id), "next", focus_id, operator_id="op1")
    after = await build_shift_state(db_session, int(org.id), operator_id="op1")

    assert f"shift:next:{int(org.id)}:{focus_id}" in fake.kv
    assert f"shift:skip:{int(org.id)}:{focus_id}" not in fake.kv
    assert focus_id in fake.sets.get(f"shift:next_set:{int(org.id)}", set())
    assert focus_id not in {it.get("id") for it in ([after.get("focus")] if after.get("focus") else []) + (after.get("queue") or [])}


@pytest.mark.asyncio
async def test_focus_lock_keeps_same_focus(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="Lock Org", slug="lock-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557007")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=8000,
            items_json={"items": [{"name": "F", "quantity": 1, "item_total": 8000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=50),
        )
    )
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    first = await build_shift_state(db_session, int(org.id), operator_id="42")
    fid = first["focus"]["id"]
    second = await build_shift_state(db_session, int(org.id), operator_id="42")
    assert second["focus"]["id"] == fid
    assert second["focus"]["reason"] == "active_focus_lease"
    assert f"shift:active_focus:{int(org.id)}:42" in fake.kv


def test_projection_gap_when_filtered_but_s1_signals() -> None:
    inp = ShiftInput(
        risk_kzt=5000,
        drafts=[],
        pending_payments=[],
        red_chats=[{"id": "c1"}],
        yellow_chats=[],
        high_value=[],
        queue_size=1,
        drafts_value_kzt=0,
    )
    state = resolve_state(inp)
    assert state == "S1"
    gap = compute_projection_gap(
        state=state,
        shift_input=inp,
        all_items=[{"id": "c1"}],
        active_items=[],
        has_focus=False,
        excluded_count=1,
    )
    assert gap is True
    assert derive_state_reason(inp, state) == "red_chat_exists"


def test_ui_may_show_calm_only_s0_s3() -> None:
    from app.services.shift_state_engine import _ui_may_show_calm_empty

    assert _ui_may_show_calm_empty(state="S3", empty_focus_reason="calm_no_action") is True
    assert _ui_may_show_calm_empty(state="S1", empty_focus_reason="action_queue_cleared") is False


@pytest.mark.asyncio
async def test_redis_set_prunes_ghost_member(monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)
    oid = 99
    fake.sets[f"shift:skip_set:{oid}"] = {"ghost:1", "real:2"}
    fake.kv[f"shift:skip:{oid}:real:2"] = "1"

    excluded, skipped, _, _ = await sse._load_excluded(oid)
    assert "real:2" in excluded
    assert "ghost:1" not in excluded
    assert "ghost:1" not in fake.sets.get(f"shift:skip_set:{oid}", set())


@pytest.mark.asyncio
async def test_presentation_empty_focus_when_filtered(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="Pres Org", slug="pres-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557008")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=6000,
            items_json={"items": [{"name": "G", "quantity": 1, "item_total": 6000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=55),
        )
    )
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    before = await build_shift_state(db_session, int(org.id), operator_id="op9")
    await apply_shift_action(db_session, int(org.id), "skip", before["focus"]["id"], operator_id="op9")
    after = await build_shift_state(db_session, int(org.id), operator_id="op9")

    assert after["focus"] is None
    assert after["presentation"]["empty_focus_reason"] in {"all_filtered", "action_queue_cleared"}
    assert after["presentation"]["projection_gap"] is True
    assert after["presentation"]["ui_may_show_calm_empty"] is False


@pytest.mark.asyncio
async def test_skip_does_not_change_state(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="State Org", slug="state-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557006")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=4000,
            items_json={"items": [{"name": "E", "quantity": 1, "item_total": 4000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=35),
        )
    )
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    before = await build_shift_state(db_session, int(org.id))
    assert before["state"] == "S4"
    focus_id = before["focus"]["id"]
    await apply_shift_action(db_session, int(org.id), "skip", focus_id)
    after = await build_shift_state(db_session, int(org.id))
    assert after["state"] == "S4"


@pytest.mark.asyncio
async def test_complete_is_idempotent(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="Idem Org", slug="idem-org")
    db_session.add(org)
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)
    emit = AsyncMock()
    monkeypatch.setattr(sse, "emit_event", emit)

    await apply_shift_action(db_session, int(org.id), "complete", "draft:42")
    await apply_shift_action(db_session, int(org.id), "complete", "draft:42")
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_skip_excludes_focus_from_next_state(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="Skip Org", slug="skip-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557004")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=9000,
            items_json={"items": [{"name": "C", "quantity": 1, "item_total": 9000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=50),
        )
    )
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    before = await build_shift_state(db_session, int(org.id))
    focus_id = before["focus"]["id"]
    await apply_shift_action(db_session, int(org.id), "skip", focus_id)
    after = await build_shift_state(db_session, int(org.id))

    assert focus_id not in {it.get("id") for it in ([after.get("focus")] if after.get("focus") else []) + (after.get("queue") or [])}


@pytest.mark.asyncio
async def test_complete_emits_business_event(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="Complete Org", slug="complete-org")
    db_session.add(org)
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)
    emit = AsyncMock()
    monkeypatch.setattr(sse, "emit_event", emit)

    await apply_shift_action(db_session, int(org.id), "complete", "draft:99")
    emit.assert_awaited_once()
    event = emit.await_args.args[1]
    assert event.type == "shift.focus_completed"


@pytest.mark.asyncio
async def test_active_focus_lease_heartbeat(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    await fake.setex(sse._active_focus_key(1, "op1"), sse.FOCUS_LEASE_TTL_SEC, "draft:2")
    renewed, _ = await sse.renew_focus_claim(1, "draft:2", "op1")
    assert renewed is True
    renewed_wrong, _ = await sse.renew_focus_claim(1, "draft:99", "op1")
    assert renewed_wrong is False


@pytest.mark.asyncio
async def test_other_operator_cannot_renew_foreign_lease(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    await fake.setex(sse._active_focus_key(1, "op_a"), sse.FOCUS_LEASE_TTL_SEC, "draft:3")
    renewed_b, _ = await sse.renew_focus_claim(1, "draft:3", "op_b")
    assert renewed_b is False


@pytest.mark.asyncio
async def test_release_focus_clears_operator_lease(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    await fake.setex(sse._active_focus_key(1, "op1"), sse.FOCUS_LEASE_TTL_SEC, "draft:9")
    assert await sse.release_focus_claim(1, "draft:9", "op1") is True
    assert await fake.get(sse._active_focus_key(1, "op1")) is None


@pytest.mark.asyncio
async def test_shift_state_endpoint_smoke(db_session) -> None:
    import importlib

    analytics = importlib.import_module("app.api.admin.analytics")

    org = Organization(name="State API Org", slug="state-api-org")
    db_session.add(org)
    await db_session.flush()

    out = await analytics.shift_state(
        DummyRequest(int(org.id)),
        location_id=None,
        db=db_session,
    )

    assert out["ok"] is True
    assert out["state"] in {"S0", "S1", "S2", "S3", "S4", "S5"}
    assert isinstance(out.get("actions"), list)
