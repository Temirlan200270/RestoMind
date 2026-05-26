"""Kitchen Gate v2 — operational mode + Decision Engine delivery checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.db.models import OperationalModeState, Organization, SystemEvent
from app.schemas.ai_schemas import AIBrainResponse, OrderItem
from app.services.operational_mode import (
    DELIVERY_MODE_PAUSED,
    KITCHEN_LOAD_BUSY,
    get_operational_mode,
    operational_mode_to_dict,
    set_operational_mode,
)


@pytest.mark.asyncio
async def test_get_operational_mode_defaults(db_with_menu) -> None:
    mode = await get_operational_mode(db_with_menu, 1)
    assert mode.kitchen_load == "normal"
    assert mode.delivery_mode == "normal"
    assert mode.force_pickup_only is False
    assert mode.prep_time_extra_min == 0


@pytest.mark.asyncio
async def test_set_and_get_operational_mode(db_with_menu) -> None:
    before, after = await set_operational_mode(
        db_with_menu,
        1,
        kitchen_load=KITCHEN_LOAD_BUSY,
        prep_time_extra_min=20,
        delivery_mode=DELIVERY_MODE_PAUSED,
        force_pickup_only=True,
        reason="Пик на кухне",
    )
    assert before.kitchen_load == "normal"
    assert after.kitchen_load == KITCHEN_LOAD_BUSY
    assert after.is_delivery_blocked is True

    loaded = await get_operational_mode(db_with_menu, 1)
    assert loaded.kitchen_load == KITCHEN_LOAD_BUSY
    assert loaded.prep_time_extra_min == 20


@pytest.mark.asyncio
async def test_expired_operational_mode_falls_back_to_default(db_with_menu) -> None:
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    db_with_menu.add(
        OperationalModeState(
            organization_id=1,
            location_id=None,
            kitchen_load=KITCHEN_LOAD_BUSY,
            delivery_mode=DELIVERY_MODE_PAUSED,
            expires_at=past,
        ),
    )
    await db_with_menu.flush()

    mode = await get_operational_mode(db_with_menu, 1)
    assert mode.kitchen_load == "normal"
    assert mode.delivery_mode == "normal"


@pytest.mark.asyncio
async def test_location_specific_mode_overrides_org_wide(db_with_menu) -> None:
    db_with_menu.add(
        OperationalModeState(
            organization_id=1,
            location_id=None,
            kitchen_load="normal",
            delivery_mode="normal",
        ),
    )
    db_with_menu.add(
        OperationalModeState(
            organization_id=1,
            location_id=5,
            kitchen_load=KITCHEN_LOAD_BUSY,
            delivery_mode=DELIVERY_MODE_PAUSED,
        ),
    )
    await db_with_menu.flush()

    loc_mode = await get_operational_mode(db_with_menu, 1, location_id=5)
    assert loc_mode.kitchen_load == KITCHEN_LOAD_BUSY
    assert loc_mode.delivery_mode == DELIVERY_MODE_PAUSED


@pytest.mark.asyncio
async def test_decision_engine_blocks_delivery_when_paused(db_with_menu) -> None:
    from app.services import operational_mode as om
    from app.services.decision_engine import decision_engine

    org = await db_with_menu.get(Organization, 1)
    assert org is not None
    await set_operational_mode(
        db_with_menu,
        1,
        delivery_mode=DELIVERY_MODE_PAUSED,
        reason="Курьеры заняты",
    )

    async def _get_mode(_db, org_id, *, location_id=None):
        return await get_operational_mode(db_with_menu, org_id, location_id=location_id)

    proposal = AIBrainResponse(
        intent="order",
        reply_text="Оформляю доставку.",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="delivery",
        delivery_address="ул. Абая 10",
    )
    ctx = type("Ctx", (), {"menu_items": [], "draft_row": None})()

    with patch.object(om, "get_operational_mode", new=_get_mode):
        result = await decision_engine.validate(proposal, ctx, org)
    assert not result.is_valid
    assert any(v.rule == "delivery_paused" for v in result.violations)


@pytest.mark.asyncio
async def test_decision_engine_blocks_delivery_when_pickup_only(db_with_menu) -> None:
    from app.services import operational_mode as om
    from app.services.decision_engine import decision_engine

    org = await db_with_menu.get(Organization, 1)
    assert org is not None
    await set_operational_mode(db_with_menu, 1, force_pickup_only=True)

    async def _get_mode(_db, org_id, *, location_id=None):
        return await get_operational_mode(db_with_menu, org_id, location_id=location_id)

    proposal = AIBrainResponse(
        intent="order",
        reply_text="Доставка.",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="delivery",
        delivery_address="ул. Абая 10",
    )
    ctx = type("Ctx", (), {"menu_items": [], "draft_row": None})()

    with patch.object(om, "get_operational_mode", new=_get_mode):
        result = await decision_engine.validate(proposal, ctx, org)
    assert not result.is_valid
    assert any(v.rule == "pickup_only" for v in result.violations)


@pytest.mark.asyncio
async def test_decision_engine_allows_pickup_when_delivery_paused(db_with_menu) -> None:
    from app.services.decision_engine import decision_engine

    org = await db_with_menu.get(Organization, 1)
    assert org is not None
    await set_operational_mode(db_with_menu, 1, delivery_mode=DELIVERY_MODE_PAUSED)

    proposal = AIBrainResponse(
        intent="order",
        reply_text="Самовывоз.",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="pickup",
    )
    ctx = type("Ctx", (), {"menu_items": [], "draft_row": None})()

    result = await decision_engine.validate(proposal, ctx, org)
    assert not any(v.rule in ("delivery_paused", "pickup_only") for v in result.violations)


@pytest.mark.asyncio
async def test_decision_engine_emits_kitchen_gate_order_blocked_event(db_with_menu) -> None:
    from app.services import operational_mode as om
    from app.services.decision_engine import (
        decision_engine,
        emit_kitchen_gate_order_blocked_events,
    )

    org = await db_with_menu.get(Organization, 1)
    assert org is not None
    await set_operational_mode(
        db_with_menu,
        1,
        delivery_mode=DELIVERY_MODE_PAUSED,
        reason="Тест блокировки",
    )

    async def _get_mode(_db, org_id, *, location_id=None):
        return await get_operational_mode(db_with_menu, org_id, location_id=location_id)

    proposal = AIBrainResponse(
        intent="order",
        reply_text="Оформляю доставку.",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="delivery",
        delivery_address="ул. Абая 10",
    )
    ctx = type("Ctx", (), {"menu_items": [], "draft_row": None})()

    with patch.object(om, "get_operational_mode", new=_get_mode):
        result = await decision_engine.validate(proposal, ctx, org)
    assert not result.is_valid

    emitted = await emit_kitchen_gate_order_blocked_events(
        db_with_menu,
        org_id=1,
        violations=result.block_violations,
        proposal=proposal,
        phone="+77001112233",
        trace_id="trace-test-kg",
    )
    assert emitted == 1

    ev = await db_with_menu.scalar(
        select(SystemEvent).where(
            SystemEvent.organization_id == 1,
            SystemEvent.event_type == "kitchen_gate.order_blocked",
        ),
    )
    assert ev is not None
    payload = ev.payload_json if isinstance(ev.payload_json, dict) else {}
    assert payload.get("block_rule") == "delivery_paused"


@pytest.mark.asyncio
async def test_kitchen_gate_expires_preset_patch(db_with_menu) -> None:
    from app.api.admin.owner_intelligence_ops import (
        KitchenGatePatchBody,
        owner_intel_kitchen_gate_patch,
    )

    class DummyRequest:
        session = {"admin_ok": True, "organization_id": 1}

    with patch(
        "app.api.admin.owner_intelligence_ops._location_scope_for_request",
        return_value=(None, False),
    ), patch(
        "app.api.admin.owner_intelligence_ops._session_staff_user",
        return_value=type("Staff", (), {"id": 42})(),
    ):
        payload = await owner_intel_kitchen_gate_patch(
            DummyRequest(),
            KitchenGatePatchBody(
                kitchen_load=KITCHEN_LOAD_BUSY,
                expires_preset="plus_30m",
            ),
            None,
            db_with_menu,
            None,
        )
    assert payload["ok"] is True
    assert payload["mode"]["expires_at"] is not None


@pytest.mark.asyncio
async def test_kitchen_gate_api_get_and_patch(db_with_menu) -> None:
    from app.api.admin.owner_intelligence_ops import (
        owner_intel_kitchen_gate_get,
        owner_intel_kitchen_gate_patch,
    )
    from app.api.admin.owner_intelligence_ops import KitchenGatePatchBody

    class DummyRequest:
        session = {"admin_ok": True, "organization_id": 1}

    with patch(
        "app.api.admin.owner_intelligence_ops._location_scope_for_request",
        return_value=(None, False),
    ), patch(
        "app.api.admin.owner_intelligence_ops._session_staff_user",
        return_value=type("Staff", (), {"id": 42})(),
    ):
        get_payload = await owner_intel_kitchen_gate_get(DummyRequest(), None, db_with_menu)
        assert get_payload["ok"] is True
        assert get_payload["mode"]["kitchen_load"] == "normal"

        patch_payload = await owner_intel_kitchen_gate_patch(
            DummyRequest(),
            KitchenGatePatchBody(
                kitchen_load=KITCHEN_LOAD_BUSY,
                delivery_mode=DELIVERY_MODE_PAUSED,
                prep_time_extra_min=15,
                reason="Тест",
            ),
            None,
            db_with_menu,
            None,
        )
        assert patch_payload["ok"] is True
        assert patch_payload["mode"]["kitchen_load"] == KITCHEN_LOAD_BUSY
        assert patch_payload["mode"]["delivery_mode"] == DELIVERY_MODE_PAUSED

    ev = await db_with_menu.scalar(
        select(SystemEvent).where(
            SystemEvent.organization_id == 1,
            SystemEvent.event_type == "kitchen_gate.mode_changed",
        ),
    )
    assert ev is not None


@pytest.mark.asyncio
async def test_operational_mode_prompt_block(db_with_menu) -> None:
    from app.services.operational_mode import format_operational_mode_for_prompt

    await set_operational_mode(
        db_with_menu,
        1,
        delivery_mode=DELIVERY_MODE_PAUSED,
        prep_time_extra_min=10,
    )
    mode = await get_operational_mode(db_with_menu, 1)
    block = format_operational_mode_for_prompt(mode)
    assert "DELIVERY_PAUSED=1" in block
    assert "PREP_TIME_EXTRA_MIN=10" in block

    payload = operational_mode_to_dict(mode)
    assert payload["is_delivery_blocked"] is True
