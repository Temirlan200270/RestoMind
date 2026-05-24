"""Sprint H: fixes from audit review (H1–H4)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


# ─── H1: DailyOrgStats new columns ───────────────────────────────────────────


class TestDailyOrgStatsH1:
    """H1: payment.*, booking.created → DailyOrgStats columns."""

    @pytest.mark.asyncio
    async def test_payment_completed_increments_column(self, db_session: AsyncSession):
        from app.services.analytics_consumer import on_business_event
        from app.services.system_events import BusinessEvent
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add(Organization(id=500, name="PayH1", slug="pay-h1"))
        await db_session.flush()

        event = BusinessEvent(
            id="payment.completed:test:1",
            org_id=500, type="payment.completed",
            actor="payment_webhook",
            payload={"order_id": 1, "amount": 2790.0},
        )
        await on_business_event(event, db_session)
        await db_session.flush()

        row = await db_session.scalar(
            select(DailyOrgStats).where(
                DailyOrgStats.organization_id == 500,
                DailyOrgStats.day == date.today(),
            )
        )
        assert row is not None
        assert row.payments_completed == 1
        assert float(row.revenue_kzt) == 2790.0

    @pytest.mark.asyncio
    async def test_payment_failed_increments_column(self, db_session: AsyncSession):
        from app.services.analytics_consumer import on_business_event
        from app.services.system_events import BusinessEvent
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add(Organization(id=501, name="PayFail", slug="pay-fail"))
        await db_session.flush()

        event = BusinessEvent(
            id="payment.failed:test:1",
            org_id=501, type="payment.failed",
            actor="payment_webhook",
            payload={"order_id": 1},
        )
        await on_business_event(event, db_session)
        await db_session.flush()

        row = await db_session.scalar(
            select(DailyOrgStats).where(DailyOrgStats.organization_id == 501)
        )
        assert row is not None
        assert row.payments_failed == 1
        assert float(row.revenue_kzt) == 0.0  # нет суммы у failed

    @pytest.mark.asyncio
    async def test_booking_created_increments_column(self, db_session: AsyncSession):
        from app.services.analytics_consumer import on_business_event
        from app.services.system_events import BusinessEvent
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add(Organization(id=502, name="BookH1", slug="book-h1"))
        await db_session.flush()

        event = BusinessEvent(
            id="booking.created:55",
            org_id=502, type="booking.created",
            actor="ai",
            payload={"booking_id": 55},
        )
        await on_business_event(event, db_session)
        await db_session.flush()

        row = await db_session.scalar(
            select(DailyOrgStats).where(DailyOrgStats.organization_id == 502)
        )
        assert row is not None
        assert row.bookings_created == 1

    @pytest.mark.asyncio
    async def test_revenue_accumulates_multiple_payments(self, db_session: AsyncSession):
        from app.services.analytics_consumer import on_business_event
        from app.services.system_events import BusinessEvent
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add(Organization(id=503, name="MultiPay", slug="multi-pay"))
        await db_session.flush()

        for i, amount in enumerate([2790.0, 1990.0, 1190.0], 1):
            event = BusinessEvent(
                id=f"payment.completed:test:{i}",
                org_id=503, type="payment.completed",
                actor="payment_webhook",
                payload={"amount": amount},
            )
            await on_business_event(event, db_session)
            await db_session.flush()

        row = await db_session.scalar(
            select(DailyOrgStats).where(DailyOrgStats.organization_id == 503)
        )
        assert row is not None
        assert row.payments_completed == 3
        assert abs(float(row.revenue_kzt) - 5970.0) < 0.01

    def test_all_10_event_types_in_event_column(self):
        from app.services.analytics_consumer import _EVENT_COLUMN, HANDLED_EVENT_TYPES
        events_with_column = set(_EVENT_COLUMN.keys())
        # payment.completed maps to payments_completed AND triggers revenue
        # so all 10 types should be covered (9 in _EVENT_COLUMN + payment.completed special)
        special_types = frozenset({
            "payment.completed",  # also triggers _upsert_daily_revenue
            "shift.focus_completed",  # _upsert_recovered (+ focus count)
            "order.draft_recovered",  # _upsert_recovered
        })
        for etype in HANDLED_EVENT_TYPES:
            has_column = etype in _EVENT_COLUMN
            is_special = etype in special_types
            assert has_column or is_special, (
                f"Event type '{etype}' has no column mapping in _EVENT_COLUMN"
            )


# ─── H2: book → faq при block ────────────────────────────────────────────────


class TestBookingBlockH2:
    """H2: billing_suspended для book → intent=faq."""

    @pytest.mark.asyncio
    async def test_booking_billing_suspended_changes_intent_to_faq(self):
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import AIBrainResponse

        proposal = AIBrainResponse(intent="book", reply_text="Бронируем")
        proposal.items = []
        proposal.order_type = ""
        proposal.delivery_address = ""
        proposal.order_actions = []

        org = MagicMock()
        org.is_active = False
        org.force_closed_until = None
        org.force_closed_reason = ""
        org.max_discount_pct = 0

        ctx = MagicMock()
        ctx.menu_items = []
        ctx.draft_row = None
        ctx.customer_ctx = ""
        ctx.user_preferences = {}

        result = await decision_engine.validate(proposal, ctx, org)

        assert not result.is_valid
        assert any(v.rule == "billing_suspended" for v in result.violations)
        assert result.corrected_response.intent == "faq", (
            "book при billing_suspended должен → faq чтобы _handle_booking не создавал бронь"
        )

    @pytest.mark.asyncio
    async def test_booking_force_closed_changes_intent_to_faq(self):
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import AIBrainResponse
        from datetime import timedelta

        proposal = AIBrainResponse(intent="book", reply_text="Бронируем")
        proposal.items = []
        proposal.order_type = ""
        proposal.delivery_address = ""
        proposal.order_actions = []

        future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        org = MagicMock()
        org.is_active = True
        org.force_closed_until = future
        org.force_closed_reason = "Закрыто"
        org.max_discount_pct = 0

        ctx = MagicMock()
        ctx.menu_items = []
        ctx.draft_row = None
        ctx.customer_ctx = ""
        ctx.user_preferences = {}

        result = await decision_engine.validate(proposal, ctx, org)

        assert not result.is_valid
        assert any(v.rule == "force_closed" for v in result.violations)
        assert result.corrected_response.intent == "faq"


# ─── H3: tenant в AIReadContext ───────────────────────────────────────────────


class TestTenantInContextH3:
    """H3: Tenant загружается в fetch_ai_read_context и передаётся в DE."""

    def test_ai_read_context_has_tenant_field(self):
        from app.services.context_engine import AIReadContext
        import dataclasses
        fields = {f.name for f in dataclasses.fields(AIReadContext)}
        assert "tenant" in fields, "AIReadContext должен содержать поле tenant"

    def test_ai_read_context_tenant_optional(self):
        from app.services.context_engine import AIReadContext
        ctx = AIReadContext(
            menu_items=[], user=None, org=None,
            kb_context="", draft_row=None, customer_ctx="",
            user_preferences={},
            # tenant не передаём → должен быть None по умолчанию
        )
        assert ctx.tenant is None

    @pytest.mark.asyncio
    async def test_de_uses_tenant_plan_status_from_context(self):
        """DE получает tenant из ctx.tenant и проверяет plan_status."""
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import AIBrainResponse
        from app.services.context_engine import AIReadContext

        tenant = MagicMock()
        tenant.plan_status = "suspended"
        tenant.is_network = False

        ctx = AIReadContext(
            menu_items=[], user=None, org=None,
            kb_context="", draft_row=None, customer_ctx="",
            user_preferences={}, tenant=tenant,
        )

        org = MagicMock()
        org.is_active = True  # org активна, но tenant suspended
        org.force_closed_until = None
        org.force_closed_reason = ""
        org.max_discount_pct = 0

        proposal = AIBrainResponse(intent="order", reply_text="Тест")
        proposal.items = []
        proposal.order_type = ""
        proposal.delivery_address = ""
        proposal.order_actions = []

        result = await decision_engine.validate(proposal, ctx, org, tenant=ctx.tenant)

        assert not result.is_valid
        block_rules = [v.rule for v in result.violations if v.severity == "block"]
        assert "billing_suspended" in block_rules, (
            "DE должен видеть tenant.plan_status=suspended даже при org.is_active=True"
        )


# ─── H4: event_driven_stats в UI ─────────────────────────────────────────────


class TestEventDrivenStatsUIH4:
    """H4: event_driven_stats используется в _tab_dashboard.html."""

    def test_dashboard_template_uses_event_driven_stats(self):
        import pathlib
        html = pathlib.Path(
            "app/templates/screens/_tab_dashboard.html"
        ).read_text(encoding="utf-8")
        assert "event_driven_stats" in html, (
            "Дашборд должен отображать данные из event_driven_stats"
        )

    def test_dashboard_shows_event_bus_indicator(self):
        import pathlib
        html = pathlib.Path(
            "app/templates/screens/_tab_dashboard.html"
        ).read_text(encoding="utf-8")
        # Бейдж «данные ОС» виден только при source=event_driven
        assert "event_driven" in html, "Должен быть индикатор источника event_driven"
        assert "payments_completed" in html, (
            "Количество оплат из event_driven_stats должно отображаться в UI"
        )
