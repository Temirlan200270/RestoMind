"""G10.2 — failure-mode simulation (multi-operator, skip spam, S1 latch)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.db.models import ChatLog, Order, OrderStatus, Organization, User
from app.services.shift_state_engine import (
    ShiftInput,
    apply_shift_action,
    build_shift_state,
    resolve_state,
    resolve_state_effective,
)


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def scan(self, cursor: int, match: str | None = None, count: int = 64) -> tuple[int, list[str]]:
        prefix = (match or "").replace("*", "")
        return 0, [k for k in self.kv if k.startswith(prefix)]

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.kv[key] = value

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


@pytest.mark.asyncio
async def test_s1_hysteresis_holds_until_exit_band(monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    enter = ShiftInput(
        risk_kzt=12000,
        drafts=[],
        pending_payments=[],
        red_chats=[],
        yellow_chats=[],
        high_value=[],
        queue_size=2,
        drafts_value_kzt=0,
    )
    state_enter, latched_enter = await resolve_state_effective(1, enter)
    assert state_enter == "S1"
    assert latched_enter is False

    dipped = ShiftInput(
        risk_kzt=8000,
        drafts=[],
        pending_payments=[],
        red_chats=[],
        yellow_chats=[],
        high_value=[],
        queue_size=2,
        drafts_value_kzt=0,
    )
    assert resolve_state(dipped) in {"S0", "S3"}
    state_hold, latched_hold = await resolve_state_effective(1, dipped)
    assert state_hold == "S1"
    assert latched_hold is True

    calm = ShiftInput(
        risk_kzt=2000,
        drafts=[],
        pending_payments=[],
        red_chats=[],
        yellow_chats=[],
        high_value=[],
        queue_size=1,
        drafts_value_kzt=0,
    )
    state_exit, latched_exit = await resolve_state_effective(1, calm)
    assert state_exit == "S3"
    assert latched_exit is False


@pytest.mark.asyncio
async def test_dual_operator_complete_single_event(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="Dual Op", slug="dual-op")
    db_session.add(org)
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)
    emit = AsyncMock()
    monkeypatch.setattr(sse, "emit_event", emit)

    fid = "draft:100"
    await apply_shift_action(db_session, int(org.id), "complete", fid, operator_id="op_a")
    await apply_shift_action(db_session, int(org.id), "complete", fid, operator_id="op_b")
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_two_operators_independent_focus_locks(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="Two Lock", slug="two-lock")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557100")
    db_session.add(user)
    await db_session.flush()
    for idx, price in enumerate((5000, 9000), start=1):
        db_session.add(
            Order(
                organization_id=int(org.id),
                user_id=int(user.id),
                status=OrderStatus.DRAFT.value,
                total_price=price,
                items_json={"items": [{"name": f"X{idx}", "quantity": 1, "item_total": price}]},
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=40 + idx),
            )
        )
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    a = await build_shift_state(db_session, int(org.id), operator_id="alice")
    b = await build_shift_state(db_session, int(org.id), operator_id="bob")
    assert a["focus"] is not None
    assert b["focus"] is not None
    assert a["focus"]["id"] != b["focus"]["id"]
    assert f"shift:active_focus:{int(org.id)}:alice" in fake.kv
    assert f"shift:active_focus:{int(org.id)}:bob" in fake.kv


@pytest.mark.asyncio
async def test_skip_spam_keeps_system_state_stable(db_session, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    org = Organization(name="Skip Spam", slug="skip-spam")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557101")
    db_session.add(user)
    await db_session.flush()
    for idx in range(3):
        db_session.add(
            Order(
                organization_id=int(org.id),
                user_id=int(user.id),
                status=OrderStatus.DRAFT.value,
                total_price=3000 + idx,
                items_json={"items": [{"name": f"I{idx}", "quantity": 1, "item_total": 3000 + idx}]},
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=35 + idx),
            )
        )
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(sse, "redis_client", fake)

    first = await build_shift_state(db_session, int(org.id), operator_id="spammer")
    system_state = first["state"]
    for _ in range(5):
        out = await build_shift_state(db_session, int(org.id), operator_id="spammer")
        if out.get("focus"):
            await apply_shift_action(
                db_session,
                int(org.id),
                "skip",
                out["focus"]["id"],
                operator_id="spammer",
            )
    final = await build_shift_state(db_session, int(org.id), operator_id="spammer")
    assert final["state"] == system_state


@pytest.mark.asyncio
async def test_red_chat_storm_stays_s1(db_session) -> None:
    org = Organization(name="Storm", slug="storm")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005557102")
    db_session.add(user)
    await db_session.flush()
    for mins in (6, 7, 8):
        db_session.add(
            ChatLog(
                organization_id=int(org.id),
                user_id=int(user.id),
                role="user",
                content=f"Жду {mins}",
                created_at=datetime.now(timezone.utc) - timedelta(minutes=mins),
            )
        )
    await db_session.flush()

    out = await build_shift_state(db_session, int(org.id), operator_id="storm")
    assert out["state"] == "S1"
    assert out["presentation"]["state_reason"] == "red_chat_exists"
