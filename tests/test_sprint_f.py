"""
Sprint F: Payment events + booking.created + event_slice (Phase 3.2).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


# ─── F1 + F2: Payment events + booking.created ───────────────────────────────


class TestPaymentAndBookingEvents:
    """Тесты F1 (payment.*) и F2 (booking.created) на emit_event."""

    def test_payment_event_types_use_dotted_notation(self):
        """payment.completed и payment.failed используют dotted-нотацию."""
        from app.services.system_events import BusinessEvent

        for etype in ["payment.completed", "payment.failed"]:
            e = BusinessEvent(
                id=f"{etype}:provider:pay_123",
                org_id=1,
                type=etype,
                actor="payment_webhook",
                payload={"order_id": 1, "provider": "cloudpayments"},
            )
            assert "." in e.type
            assert "_" not in e.type

    @pytest.mark.asyncio
    async def test_payment_completed_saved_to_system_events(self, db_session: AsyncSession):
        """payment.completed через emit_event записывается в system_events."""
        from sqlalchemy import select
        from app.services.system_events import BusinessEvent, emit_event
        from app.db.models import Organization, SystemEvent

        db_session.add(Organization(id=80, name="PayOrg", slug="pay-org"))
        await db_session.flush()

        event = BusinessEvent(
            id="payment.completed:cloudpayments:pay_777",
            org_id=80,
            type="payment.completed",
            actor="payment_webhook",
            entity_type="order",
            entity_id=100,
            payload={"order_id": 100, "provider": "cloudpayments", "amount": 2790.0},
        )
        result = await emit_event(db_session, event)
        await db_session.flush()

        assert result is not None
        row = await db_session.scalar(
            select(SystemEvent).where(SystemEvent.id == result.id)
        )
        assert row is not None
        assert row.event_type == "payment.completed"
        assert row.source == "payment_webhook"
        assert row.payload_json["order_id"] == 100

    @pytest.mark.asyncio
    async def test_payment_failed_idempotent(self, db_session: AsyncSession):
        """Два payment.failed с одним id → только одна запись."""
        from sqlalchemy import func, select
        from app.services.system_events import BusinessEvent, emit_event
        from app.db.models import Organization, SystemEvent

        db_session.add(Organization(id=81, name="PayOrg2", slug="pay-org2"))
        await db_session.flush()

        fixed_id = "payment.failed:kaspi:tx_999"
        e = BusinessEvent(
            id=fixed_id, org_id=81, type="payment.failed",
            actor="payment_webhook", payload={"order_id": 2},
        )
        await emit_event(db_session, e)
        await db_session.flush()
        r2 = await emit_event(db_session, e)
        await db_session.flush()

        assert r2 is None

        count_rows = await db_session.scalar(
            select(func.count()).select_from(SystemEvent)
            .where(SystemEvent.idempotency_key == fixed_id)
        )
        assert count_rows == 1

    @pytest.mark.asyncio
    async def test_booking_created_event_on_bus(self, db_session: AsyncSession):
        """booking.created через emit_event сохраняется с правильными полями."""
        from sqlalchemy import select
        from app.services.system_events import BusinessEvent, emit_event
        from app.db.models import Organization, SystemEvent

        db_session.add(Organization(id=82, name="BookOrg", slug="book-org"))
        await db_session.flush()

        event = BusinessEvent(
            id="booking.created:55",
            org_id=82,
            type="booking.created",
            actor="ai",
            entity_type="booking",
            entity_id=55,
            payload={"booking_id": 55, "date": "2026-06-01", "guests": 4},
        )
        result = await emit_event(db_session, event)
        await db_session.flush()

        assert result is not None
        row = await db_session.scalar(
            select(SystemEvent).where(SystemEvent.id == result.id)
        )
        assert row is not None
        assert row.event_type == "booking.created"
        assert row.entity_type == "booking"

    def test_all_business_events_in_consumer_handled_types(self):
        """Все 10 типов событий зарегистрированы в analytics_consumer."""
        from app.services.analytics_consumer import HANDLED_EVENT_TYPES

        expected = {
            "order.created", "order.confirmed", "order.cancelled",
            "booking.created", "booking.confirmed", "booking.cancelled",
            "ai.escalated", "operator.took_over",
            "payment.completed", "payment.failed",
        }
        for event_type in expected:
            assert event_type in HANDLED_EVENT_TYPES, (
                f"Event type '{event_type}' missing from HANDLED_EVENT_TYPES"
            )


# ─── F3: event_slice (Phase 3.2) ─────────────────────────────────────────────


class TestEventSlice:
    """Тесты Phase 3.2: event_slice в AI Context Snapshot."""

    @pytest.mark.asyncio
    async def test_event_slice_populated_from_system_events(self, db_session: AsyncSession):
        """_load_recent_event_slice возвращает события за последние 15 мин."""
        from app.services.context_engine import _load_recent_event_slice
        from app.db.models import Organization, SystemEvent

        db_session.add(Organization(id=90, name="SliceOrg", slug="slice-org"))
        await db_session.flush()

        ev = SystemEvent(
            organization_id=90,
            event_type="order.created",
            source="ai",
            payload_json={"order_id": 10},
        )
        db_session.add(ev)
        await db_session.flush()

        result = await _load_recent_event_slice(db_session, 90, minutes=15)

        assert result["events_count"] >= 1
        assert result["window_minutes"] == 15
        assert isinstance(result["events"], list)
        assert any(e["type"] == "order.created" for e in result["events"])

    @pytest.mark.asyncio
    async def test_event_slice_excludes_other_org(self, db_session: AsyncSession):
        """event_slice не включает события другой организации (изоляция)."""
        from app.services.context_engine import _load_recent_event_slice
        from app.db.models import Organization, SystemEvent

        db_session.add_all([
            Organization(id=91, name="SliceA", slug="slice-a"),
            Organization(id=92, name="SliceB", slug="slice-b"),
        ])
        await db_session.flush()

        db_session.add(SystemEvent(
            organization_id=92,
            event_type="ai.escalated",
            source="ai",
            payload_json={},
        ))
        await db_session.flush()

        result = await _load_recent_event_slice(db_session, 91, minutes=15)

        assert result["events_count"] == 0

    @pytest.mark.asyncio
    async def test_event_slice_removes_private_payload_keys(self, db_session: AsyncSession):
        """Служебные _actor, _version убираются из payload в event_slice."""
        from app.services.context_engine import _load_recent_event_slice
        from app.db.models import Organization, SystemEvent

        db_session.add(Organization(id=93, name="CleanOrg", slug="clean-org"))
        await db_session.flush()

        db_session.add(SystemEvent(
            organization_id=93,
            event_type="order.confirmed",
            source="ai",
            payload_json={
                "order_id": 5,
                "_actor": "ai",
                "_version": 1,
                "total_price": 2790.0,
            },
        ))
        await db_session.flush()

        result = await _load_recent_event_slice(db_session, 93, minutes=15)

        assert result["events_count"] == 1
        payload = result["events"][0]["payload"]
        assert "_actor" not in payload
        assert "_version" not in payload
        assert "order_id" in payload
        assert "total_price" in payload

    @pytest.mark.asyncio
    async def test_event_slice_respects_time_window(self, db_session: AsyncSession):
        """Устаревшие события (> window_minutes) не включаются."""
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import update
        from app.services.context_engine import _load_recent_event_slice
        from app.db.models import Organization, SystemEvent

        db_session.add(Organization(id=94, name="OldOrg", slug="old-org"))
        await db_session.flush()

        ev = SystemEvent(
            organization_id=94,
            event_type="order.cancelled",
            source="ai",
            payload_json={},
        )
        db_session.add(ev)
        await db_session.flush()

        old_ts = datetime.now(tz=timezone.utc) - timedelta(minutes=60)
        await db_session.execute(
            update(SystemEvent)
            .where(SystemEvent.id == ev.id)
            .values(created_at=old_ts)
        )
        await db_session.flush()

        result = await _load_recent_event_slice(db_session, 94, minutes=15)
        assert result["events_count"] == 0

    @pytest.mark.asyncio
    async def test_event_slice_chronological_order(self, db_session: AsyncSession):
        """События в event_slice возвращаются в хронологическом порядке."""
        from app.services.context_engine import _load_recent_event_slice
        from app.db.models import Organization, SystemEvent

        db_session.add(Organization(id=95, name="OrderedOrg", slug="ordered-org"))
        await db_session.flush()

        for etype in ["order.created", "order.confirmed", "ai.escalated"]:
            db_session.add(SystemEvent(
                organization_id=95, event_type=etype, source="ai", payload_json={},
            ))
            await db_session.flush()

        result = await _load_recent_event_slice(db_session, 95, minutes=15)

        types_in_order = [e["type"] for e in result["events"]]
        assert types_in_order == sorted(
            types_in_order,
            key=lambda t: result["events"][types_in_order.index(t)].get("ts") or ""
        ), "Events should be in chronological order"

    def test_event_slice_returns_correct_structure(self):
        """_load_recent_event_slice возвращает обязательные ключи даже при ошибке."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from app.services.context_engine import _load_recent_event_slice

        # Ошибка в execute — должен вернуть dict с error
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB error"))

        result = asyncio.run(
            _load_recent_event_slice(mock_db, 1, minutes=15)
        )

        assert "window_minutes" in result
        assert "events_count" in result
        assert "events" in result
        assert result["events_count"] == 0


# ─── Итоговая таблица покрытия событий ───────────────────────────────────────


class TestEventCoverage:
    """Проверяет что все события покрыты end-to-end."""

    def test_event_type_taxonomy_complete(self):
        """Проверяем полноту таксономии событий (10 типов)."""
        from app.services.analytics_consumer import HANDLED_EVENT_TYPES

        # Phase 2 events (заказы + брони)
        assert "order.created" in HANDLED_EVENT_TYPES
        assert "order.confirmed" in HANDLED_EVENT_TYPES
        assert "order.cancelled" in HANDLED_EVENT_TYPES
        assert "booking.created" in HANDLED_EVENT_TYPES
        assert "booking.confirmed" in HANDLED_EVENT_TYPES
        assert "booking.cancelled" in HANDLED_EVENT_TYPES

        # Phase 2 events (оператор + ИИ)
        assert "ai.escalated" in HANDLED_EVENT_TYPES
        assert "operator.took_over" in HANDLED_EVENT_TYPES

        # Phase 2 events (деньги — критичный audit point)
        assert "payment.completed" in HANDLED_EVENT_TYPES
        assert "payment.failed" in HANDLED_EVENT_TYPES

    def test_no_legacy_underscore_events_in_handled_types(self):
        """В HANDLED_EVENT_TYPES нет старых событий с underscore-нотацией."""
        from app.services.analytics_consumer import HANDLED_EVENT_TYPES

        legacy_types = {
            "order_created", "order_confirmed", "order_cancelled",
            "payment_completed", "payment_failed", "booking_confirmed",
        }
        for legacy in legacy_types:
            assert legacy not in HANDLED_EVENT_TYPES, (
                f"Legacy event type '{legacy}' found — should use dotted notation"
            )
