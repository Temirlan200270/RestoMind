from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import Order, OrderStatus, Organization, User
from app.services.draft_recovery import (
    DRAFT_RECOVERY_DEDUPE_SEC,
    _dedupe_key,
    run_draft_recovery_for_org,
    send_draft_recovery_nudge,
)


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        if ex is not None:
            self.ttl[key] = ex
        return True

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.kv[key] = value
        self.ttl[key] = ttl

    async def delete(self, key: str) -> None:
        self.kv.pop(key, None)
        self.ttl.pop(key, None)


@pytest.mark.asyncio
async def test_send_draft_recovery_nudge_dedupes_for_24h(db_session, monkeypatch) -> None:
    from app.services import draft_recovery as dr_mod

    org = Organization(name="Recovery Org", slug="recovery-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005550901", current_state="chatting")
    db_session.add(user)
    await db_session.flush()
    order = Order(
        organization_id=int(org.id),
        user_id=int(user.id),
        status=OrderStatus.DRAFT.value,
        total_price=4500,
        items_json={"items": [{"name": "Плов", "quantity": 1, "item_total": 4500}]},
        updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(order)
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(dr_mod, "redis_client", fake)
    monkeypatch.setattr(
        dr_mod,
        "_restore_confirming_state",
        AsyncMock(),
    )

    send_buttons = AsyncMock(return_value=type("R", (), {"ok": True})())
    monkeypatch.setattr(
        "app.integrations.whatsapp.send_interactive_buttons",
        send_buttons,
    )
    emit = AsyncMock()
    monkeypatch.setattr(dr_mod, "emit_event", emit)

    ok1 = await send_draft_recovery_nudge(
        db_session,
        order=order,
        phone=user.phone,
        org_id=int(org.id),
    )
    ok2 = await send_draft_recovery_nudge(
        db_session,
        order=order,
        phone=user.phone,
        org_id=int(org.id),
    )

    assert ok1 is True
    assert ok2 is False
    assert send_buttons.await_count == 1
    assert fake.kv[_dedupe_key(order.id)] == "1"
    assert fake.ttl[_dedupe_key(order.id)] == DRAFT_RECOVERY_DEDUPE_SEC
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_draft_recovery_skips_human_mode_and_fresh_drafts(db_session, monkeypatch) -> None:
    from app.services import draft_recovery as dr_mod

    org = Organization(name="Recovery Org 2", slug="recovery-org-2", is_active=True)
    db_session.add(org)
    await db_session.flush()

    user_human = User(
        organization_id=int(org.id),
        phone="+77005550902",
        current_state="human_mode",
    )
    user_ok = User(
        organization_id=int(org.id),
        phone="+77005550903",
        current_state="chatting",
    )
    db_session.add_all([user_human, user_ok])
    await db_session.flush()

    stale = Order(
        organization_id=int(org.id),
        user_id=int(user_human.id),
        status=OrderStatus.DRAFT.value,
        total_price=3000,
        items_json={"items": [{"name": "Лагман", "quantity": 1, "item_total": 3000}]},
        updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    fresh = Order(
        organization_id=int(org.id),
        user_id=int(user_ok.id),
        status=OrderStatus.DRAFT.value,
        total_price=2000,
        items_json={"items": [{"name": "Чай", "quantity": 1, "item_total": 2000}]},
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    recoverable = Order(
        organization_id=int(org.id),
        user_id=int(user_ok.id),
        status=OrderStatus.DRAFT.value,
        total_price=5000,
        items_json={"items": [{"name": "Плов", "quantity": 1, "item_total": 5000}]},
        updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add_all([stale, fresh, recoverable])
    await db_session.flush()

    fake = FakeRedis()
    monkeypatch.setattr(dr_mod, "redis_client", fake)
    nudge = AsyncMock(side_effect=[True])
    monkeypatch.setattr(dr_mod, "send_draft_recovery_nudge", nudge)

    sent = await run_draft_recovery_for_org(db_session, int(org.id))

    assert sent == 1
    nudge.assert_awaited_once()
    args, kwargs = nudge.await_args
    assert kwargs["order"].id == recoverable.id


@pytest.mark.asyncio
async def test_draft_recovery_buttons_map_to_confirm_flow() -> None:
    import pathlib

    recovery = pathlib.Path("app/services/draft_recovery.py").read_text(encoding="utf-8")
    webhooks = pathlib.Path("app/api/webhooks.py").read_text(encoding="utf-8")
    assert '"confirm"' in recovery
    assert 'btn_id == "confirm"' in webhooks
    assert 'message_text = "да"' in webhooks
