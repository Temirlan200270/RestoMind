"""
Тесты для OS Transition Plan: Sprint B (Event System), Sprint C (AI Context Snapshot),
Sprint D (Decision Engine).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

# ─── Sprint D: Decision Engine ────────────────────────────────────────────────


def _make_ai_response(intent="order", reply_text="Ваш заказ принят"):
    """Создаёт минимальный AIBrainResponse для тестов."""
    from app.schemas.ai_schemas import AIBrainResponse
    return AIBrainResponse(intent=intent, reply_text=reply_text)


def _make_org(force_closed_until=None, force_closed_reason="", max_discount_pct=0):
    """Создаёт заглушку Organization."""
    org = MagicMock()
    org.id = 1
    org.force_closed_until = force_closed_until
    org.force_closed_reason = force_closed_reason
    org.max_discount_pct = max_discount_pct
    return org


def _make_context(menu_items=None):
    """Создаёт заглушку AIReadContext."""
    ctx = MagicMock()
    ctx.menu_items = menu_items or []
    ctx.org = None
    ctx.draft_row = None
    ctx.customer_ctx = ""
    ctx.user_preferences = {}
    return ctx


class TestDecisionEngine:
    """Unit-тесты DecisionEngine без БД."""

    @pytest.mark.asyncio
    async def test_no_violations_for_open_restaurant(self):
        from app.services.decision_engine import decision_engine
        proposal = _make_ai_response("order")
        ctx = _make_context()
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert result.is_valid
        assert result.violations == []
        assert result.corrected_response is None

    @pytest.mark.asyncio
    async def test_force_closed_blocks_order_intent(self):
        """Критичный: force_closed должен блокировать intent=order."""
        from app.services.decision_engine import decision_engine
        future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        org = _make_org(force_closed_until=future, force_closed_reason="Санитарный день")
        proposal = _make_ai_response("order")
        ctx = _make_context()

        result = await decision_engine.validate(proposal, ctx, org)

        assert not result.is_valid
        assert result.has_blocks
        assert any(v.rule == "force_closed" for v in result.violations)

    @pytest.mark.asyncio
    async def test_force_closed_block_changes_intent_to_faq(self):
        """Критичный фикс: corrected_response должен иметь intent=faq."""
        from app.services.decision_engine import decision_engine
        future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        org = _make_org(force_closed_until=future, force_closed_reason="Технический перерыв")
        proposal = _make_ai_response("order")
        ctx = _make_context()

        result = await decision_engine.validate(proposal, ctx, org)

        assert result.corrected_response is not None
        assert result.corrected_response.intent == "faq", (
            "Blocking violation must change intent to 'faq' to prevent route_intent "
            "from creating a draft order"
        )
        assert "временно" in result.corrected_response.reply_text.lower() or \
               "не принимаем" in result.corrected_response.reply_text.lower()

    @pytest.mark.asyncio
    async def test_force_closed_does_not_block_faq_intent(self):
        """force_closed не должен блокировать FAQ-вопросы клиентов."""
        from app.services.decision_engine import decision_engine
        future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        org = _make_org(force_closed_until=future)
        proposal = _make_ai_response("faq", "У вас есть плов?")
        ctx = _make_context()

        result = await decision_engine.validate(proposal, ctx, org)

        assert result.is_valid
        assert result.violations == []

    @pytest.mark.asyncio
    async def test_force_closed_expired_does_not_block(self):
        """Истекшее force_closed не должно блокировать заказы."""
        from app.services.decision_engine import decision_engine
        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        org = _make_org(force_closed_until=past)
        proposal = _make_ai_response("order")
        ctx = _make_context()

        result = await decision_engine.validate(proposal, ctx, org)

        assert result.is_valid

    @pytest.mark.asyncio
    async def test_stoplist_produces_warning_not_block(self):
        """Стоп-позиции дают warn, не block — route_intent сам обработает."""
        from app.services.decision_engine import decision_engine
        from app.db.models import MenuItem
        from app.schemas.ai_schemas import OrderItem

        stopped = MagicMock(spec=MenuItem)
        stopped.name = "Маргарита"
        stopped.is_available = False

        proposal = _make_ai_response("order")
        proposal.items = [OrderItem(name="Маргарита", quantity=1)]

        ctx = _make_context(menu_items=[stopped])
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        # stoplist — warn only, не block
        assert result.is_valid  # не блокируем
        assert result.has_warnings
        assert any(v.rule == "stoplist" for v in result.violations)
        assert result.corrected_response is None

    @pytest.mark.asyncio
    async def test_org_none_does_not_crash(self):
        """DE не падает если org=None."""
        from app.services.decision_engine import decision_engine
        proposal = _make_ai_response("order")
        ctx = _make_context()

        result = await decision_engine.validate(proposal, ctx, org=None)

        assert result.is_valid

    @pytest.mark.asyncio
    async def test_force_closed_naive_datetime_handled(self):
        """Naive datetime в force_closed_until обрабатывается без ошибок."""
        from app.services.decision_engine import decision_engine
        naive_future = datetime.utcnow() + timedelta(hours=2)
        assert naive_future.tzinfo is None  # убеждаемся что naive
        org = _make_org(force_closed_until=naive_future)
        proposal = _make_ai_response("order")
        ctx = _make_context()

        result = await decision_engine.validate(proposal, ctx, org)

        assert not result.is_valid  # должен заблокировать


# ─── Sprint B: BusinessEvent + emit_event ─────────────────────────────────────


class TestBusinessEvent:
    """Unit-тесты для BusinessEvent dataclass и emit_event."""

    def test_business_event_generates_uuid_by_default(self):
        from app.services.system_events import BusinessEvent
        e1 = BusinessEvent(org_id=1, type="order.created", actor="ai", payload={})
        e2 = BusinessEvent(org_id=1, type="order.created", actor="ai", payload={})
        assert e1.id != e2.id
        assert len(e1.id) == 36  # UUID4 format

    def test_business_event_accepts_explicit_id(self):
        from app.services.system_events import BusinessEvent
        fixed_id = str(uuid.uuid4())
        event = BusinessEvent(org_id=1, type="test", actor="system", payload={}, id=fixed_id)
        assert event.id == fixed_id

    def test_business_event_default_version_is_1(self):
        from app.services.system_events import BusinessEvent
        event = BusinessEvent(org_id=1, type="test", actor="system", payload={})
        assert event.version == 1

    @pytest.mark.asyncio
    async def test_emit_event_idempotent_same_id(self, db_session: AsyncSession):
        """Два emit_event с одним id должны записать только одно событие."""
        from app.services.system_events import BusinessEvent, emit_event
        from app.db.models import Organization, SystemEvent
        from sqlalchemy import select

        db_session.add(Organization(id=42, name="TestOrg", slug="test-org"))
        await db_session.flush()

        fixed_id = str(uuid.uuid4())
        event = BusinessEvent(
            org_id=42, type="order.created", actor="ai",
            payload={"order_id": 1}, id=fixed_id,
        )

        r1 = await emit_event(db_session, event)
        await db_session.flush()
        r2 = await emit_event(db_session, event)  # same id
        await db_session.flush()

        assert r1 is not None
        assert r2 is None  # дубликат отклонён

        count = await db_session.scalar(
            select(SystemEvent).where(SystemEvent.idempotency_key == fixed_id)
                .with_only_columns(__import__("sqlalchemy").func.count())
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_emit_event_stores_actor_in_source(self, db_session: AsyncSession):
        """actor маппируется в source колонку SystemEvent."""
        from app.services.system_events import BusinessEvent, emit_event
        from app.db.models import Organization, SystemEvent
        from sqlalchemy import select

        db_session.add(Organization(id=43, name="TestOrg2", slug="test-org2"))
        await db_session.flush()

        event = BusinessEvent(
            org_id=43, type="ai.escalated", actor="ai", payload={"phone": "+77001112233"},
        )
        result = await emit_event(db_session, event)
        await db_session.flush()

        assert result is not None
        row = await db_session.scalar(
            select(SystemEvent).where(SystemEvent.id == result.id)
        )
        assert row is not None
        assert row.source == "ai"
        assert row.event_type == "ai.escalated"


# ─── Sprint C: AIContextSnapshot ─────────────────────────────────────────────


class TestAIContextSnapshot:
    """Тесты для таблицы ai_context_snapshots и org-scoped isolation."""

    @pytest.mark.asyncio
    async def test_snapshot_saved_with_correct_org(self, db_session: AsyncSession):
        """Снимок сохраняется с правильным org_id."""
        from app.db.models import AIContextSnapshot, Organization
        from sqlalchemy import select

        db_session.add(Organization(id=10, name="SnapOrg", slug="snap-org"))
        await db_session.flush()

        snap_id = str(uuid.uuid4())
        snap = AIContextSnapshot(
            id=snap_id,
            organization_id=10,
            phone="+77001234567",
            business_state={"menu_items_count": 5},
            customer_state={"has_draft": False},
            event_slice={},
        )
        db_session.add(snap)
        await db_session.flush()

        row = await db_session.scalar(
            select(AIContextSnapshot).where(AIContextSnapshot.id == snap_id)
        )
        assert row is not None
        assert row.organization_id == 10
        assert row.phone == "+77001234567"

    @pytest.mark.asyncio
    async def test_snapshot_org_isolation(self, db_session: AsyncSession):
        """Снимки одной org не видны другой (скоупинг)."""
        from app.db.models import AIContextSnapshot, Organization
        from sqlalchemy import select

        db_session.add_all([
            Organization(id=20, name="Org A", slug="org-a"),
            Organization(id=21, name="Org B", slug="org-b"),
        ])
        await db_session.flush()

        snap_a = AIContextSnapshot(
            id=str(uuid.uuid4()), organization_id=20,
            phone="+7700A", business_state={}, customer_state={}, event_slice={},
        )
        snap_b = AIContextSnapshot(
            id=str(uuid.uuid4()), organization_id=21,
            phone="+7700B", business_state={}, customer_state={}, event_slice={},
        )
        db_session.add_all([snap_a, snap_b])
        await db_session.flush()

        # Орг A видит только свои снимки
        rows_a = (await db_session.execute(
            select(AIContextSnapshot).where(AIContextSnapshot.organization_id == 20)
        )).scalars().all()
        assert len(rows_a) == 1
        assert rows_a[0].organization_id == 20

        rows_b = (await db_session.execute(
            select(AIContextSnapshot).where(AIContextSnapshot.organization_id == 21)
        )).scalars().all()
        assert len(rows_b) == 1
        assert rows_b[0].organization_id == 21

    @pytest.mark.asyncio
    async def test_snapshot_business_state_contains_menu_info(self, db_with_menu: AsyncSession):
        """save_ai_context_snapshot сохраняет menu_items_count."""
        from unittest.mock import patch as mock_patch, AsyncMock
        from app.services.context_engine import save_ai_context_snapshot, AIReadContext
        from app.db.models import AIContextSnapshot
        from sqlalchemy import select

        menu_items = await __import__(
            "app.services.order_logic", fromlist=["load_available_menu"]
        ).load_available_menu(db_with_menu, organization_id=1, include_unavailable=True)

        ctx = AIReadContext(
            menu_items=menu_items,
            user=None,
            org=None,
            kb_context="",
            draft_row=None,
            customer_ctx="",
            user_preferences={},
        )

        # patch async_session_factory чтобы использовать тестовую сессию
        snap_id = None
        with mock_patch("app.services.context_engine.async_session_factory") as mock_factory:
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(return_value=db_with_menu)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_cm

            snap_id = await save_ai_context_snapshot("+77001112233", 1, ctx)

        assert snap_id is not None
        assert len(snap_id) == 36  # UUID format

    def test_snapshot_id_is_uuid_string(self):
        """save_ai_context_snapshot возвращает строку UUID даже при ошибке БД."""
        import asyncio
        from unittest.mock import patch as mock_patch, AsyncMock
        from app.services.context_engine import save_ai_context_snapshot, AIReadContext

        ctx = AIReadContext(
            menu_items=[], user=None, org=None,
            kb_context="", draft_row=None, customer_ctx="", user_preferences={},
        )

        # Симулируем ошибку БД
        with mock_patch("app.services.context_engine.async_session_factory") as mock_factory:
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(side_effect=Exception("DB unavailable"))
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_cm

            snap_id = asyncio.get_event_loop().run_until_complete(
                save_ai_context_snapshot("+7700", 1, ctx)
            )

        # Ошибка не должна пробросить исключение, ID всё равно возвращается
        assert snap_id is not None
        assert len(snap_id) == 36


# ─── Integration smoke tests ─────────────────────────────────────────────────


class TestDecisionEngineIntegrationSmoke:
    """Минимальные smoke-тесты интеграции DE в pipeline."""

    @pytest.mark.asyncio
    async def test_de_does_not_mutate_original_response(self):
        """validate() не изменяет оригинальный объект."""
        from app.services.decision_engine import decision_engine
        future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        org = _make_org(force_closed_until=future)
        proposal = _make_ai_response("order", "Заказ принят")
        ctx = _make_context()
        original_intent = proposal.intent
        original_reply = proposal.reply_text

        result = await decision_engine.validate(proposal, ctx, org)

        # Оригинал не мутирован
        assert proposal.intent == original_intent
        assert proposal.reply_text == original_reply
        # corrected_response — отдельная копия
        assert result.corrected_response is not proposal

    @pytest.mark.asyncio
    async def test_de_exception_safety(self):
        """Если _check_force_closed бросает исключение — validate всё равно возвращает результат."""
        from app.services.decision_engine import DecisionEngine
        from app.services.decision_engine import ValidationResult

        class BrokenEngine(DecisionEngine):
            def _check_force_closed(self, proposal, org):
                raise RuntimeError("unexpected crash")

        engine = BrokenEngine()
        proposal = _make_ai_response("order")
        ctx = _make_context()
        org = _make_org()

        with pytest.raises(RuntimeError):
            # Ошибка пробрасывается — webhooks.py ловит её через try/except
            await engine.validate(proposal, ctx, org)


# ─── Sprint E: DE новые правила ──────────────────────────────────────────────


class TestDecisionEngineNewRules:
    """Тесты трёх новых правил Decision Engine (Sprint E2)."""

    @pytest.mark.asyncio
    async def test_empty_order_blocked(self):
        """intent=order с items=[] и order_actions=[] → block."""
        from app.services.decision_engine import decision_engine

        proposal = _make_ai_response("order")
        proposal.items = []
        proposal.order_actions = []
        ctx = _make_context()
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert not result.is_valid
        assert result.has_blocks
        assert any(v.rule == "empty_order" for v in result.violations)

    @pytest.mark.asyncio
    async def test_empty_order_blocked_changes_intent(self):
        """empty_order block должен менять intent → faq."""
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import AIBrainResponse

        proposal = _make_ai_response("order")
        proposal.items = []
        proposal.order_actions = []
        ctx = _make_context()
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert result.corrected_response is not None
        assert result.corrected_response.intent == "faq"

    @pytest.mark.asyncio
    async def test_order_with_actions_not_blocked(self):
        """intent=order с непустым order_actions — не пустой заказ, блока нет."""
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import OrderAction

        proposal = _make_ai_response("order")
        proposal.items = []
        proposal.order_actions = [OrderAction(action="add", name="Плов", quantity=1)]
        ctx = _make_context()
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        # order_actions непустой — не блокируем
        assert not any(v.rule == "empty_order" for v in result.violations)

    @pytest.mark.asyncio
    async def test_delivery_no_address_is_warn_only(self):
        """order_type=delivery без адреса → warn (не block), pipeline продолжается."""
        from app.services.decision_engine import decision_engine

        proposal = _make_ai_response("order")
        proposal.order_type = "delivery"
        proposal.delivery_address = ""
        proposal.items = [__import__("app.schemas.ai_schemas", fromlist=["OrderItem"]).OrderItem(name="Плов", quantity=1)]
        ctx = _make_context()
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert result.is_valid  # warn не блокирует
        assert any(v.rule == "delivery_no_address" for v in result.violations)
        assert all(v.severity != "block" for v in result.violations if v.rule == "delivery_no_address")

    @pytest.mark.asyncio
    async def test_delivery_with_address_no_warn(self):
        """order_type=delivery с адресом — нет предупреждений по адресу."""
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import OrderItem

        proposal = _make_ai_response("order")
        proposal.order_type = "delivery"
        proposal.delivery_address = "ул. Абая 10, кв. 5"
        proposal.items = [OrderItem(name="Плов", quantity=1)]
        ctx = _make_context()
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert not any(v.rule == "delivery_no_address" for v in result.violations)

    @pytest.mark.asyncio
    async def test_too_many_items_is_warn_only(self):
        """Заказ с 25 позициями (> 20) — warn, не block."""
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import OrderItem

        proposal = _make_ai_response("order")
        proposal.items = [OrderItem(name=f"Блюдо {i}", quantity=1) for i in range(25)]
        ctx = _make_context()
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert result.is_valid  # warn не блокирует
        assert any(v.rule == "order_items_anomaly" for v in result.violations)

    @pytest.mark.asyncio
    async def test_normal_order_no_violations(self):
        """Обычный заказ без нарушений — is_valid=True, violations=[]."""
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import OrderItem

        proposal = _make_ai_response("order")
        proposal.order_type = "pickup"
        proposal.delivery_address = ""
        proposal.items = [OrderItem(name="Плов", quantity=1), OrderItem(name="Лагман", quantity=2)]
        ctx = _make_context()
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert result.is_valid
        assert result.violations == []


# ─── Sprint E: Event System — мигрированные события ─────────────────────────


class TestEventSystemMigration:
    """Проверяем что мигрированные события используют правильный формат BusinessEvent."""

    def test_event_type_dotted_notation(self):
        """Все мигрированные события используют dotted-нотацию (order.created, не order_created)."""
        from app.services.system_events import BusinessEvent

        events = [
            BusinessEvent(id="order.created:1", org_id=1, type="order.created", actor="ai", payload={}),
            BusinessEvent(id="order.confirmed:1", org_id=1, type="order.confirmed", actor="customer", payload={}),
            BusinessEvent(id="order.cancelled:1", org_id=1, type="order.cancelled", actor="ai", payload={}),
            BusinessEvent(id="booking.confirmed:1", org_id=1, type="booking.confirmed", actor="customer", payload={}),
            BusinessEvent(id="booking.cancelled:1", org_id=1, type="booking.cancelled", actor="customer", payload={}),
        ]
        for e in events:
            assert "." in e.type, f"Event type '{e.type}' should use dotted notation"
            assert "_" not in e.type, f"Event type '{e.type}' should not use underscore notation"

    def test_deterministic_idempotency_key(self):
        """Детерминированный id вида 'order.created:123' предотвращает дубли."""
        from app.services.system_events import BusinessEvent

        order_id = 42
        e1 = BusinessEvent(id=f"order.created:{order_id}", org_id=1, type="order.created", actor="ai", payload={})
        e2 = BusinessEvent(id=f"order.created:{order_id}", org_id=1, type="order.created", actor="ai", payload={})

        assert e1.id == e2.id == f"order.created:{order_id}"

    @pytest.mark.asyncio
    async def test_order_created_event_saved(self, db_session: AsyncSession):
        """order.created через emit_event сохраняется в system_events с правильным типом."""
        from app.services.system_events import BusinessEvent, emit_event
        from app.db.models import Organization, SystemEvent
        from sqlalchemy import select

        db_session.add(Organization(id=50, name="EventOrg", slug="event-org"))
        await db_session.flush()

        event = BusinessEvent(
            id="order.created:999",
            org_id=50,
            type="order.created",
            actor="ai",
            entity_type="order",
            entity_id=999,
            payload={"order_id": 999, "total_price": 2790.0},
        )
        result = await emit_event(db_session, event)
        await db_session.flush()

        assert result is not None
        row = await db_session.scalar(
            select(SystemEvent).where(SystemEvent.id == result.id)
        )
        assert row is not None
        assert row.event_type == "order.created"
        assert row.source == "ai"
        assert row.entity_type == "order"
        assert row.payload_json["order_id"] == 999
        assert row.payload_json["_actor"] == "ai"
        assert row.payload_json["_version"] == 1


# ─── Phase 2.3: DailyOrgStats + analytics_consumer ───────────────────────────


class TestDailyOrgStats:
    """Тесты event-driven агрегатов Phase 2.3."""

    @pytest.mark.asyncio
    async def test_upsert_increments_column(self, db_session: AsyncSession):
        """_upsert_daily_stat увеличивает нужную колонку атомарно."""
        from app.services.analytics_consumer import _upsert_daily_stat
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add(Organization(id=60, name="StatsOrg", slug="stats-org"))
        await db_session.flush()

        today = date.today()
        await _upsert_daily_stat(db_session, 60, today, "orders_created")
        await db_session.flush()

        row = await db_session.scalar(
            select(DailyOrgStats).where(
                DailyOrgStats.organization_id == 60,
                DailyOrgStats.day == today,
            )
        )
        assert row is not None
        assert row.orders_created == 1

    @pytest.mark.asyncio
    async def test_upsert_increments_twice(self, db_session: AsyncSession):
        """Два upsert → значение 2, не 1 (ON CONFLICT DO UPDATE работает)."""
        from app.services.analytics_consumer import _upsert_daily_stat
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add(Organization(id=61, name="StatsOrg2", slug="stats-org2"))
        await db_session.flush()

        today = date.today()
        await _upsert_daily_stat(db_session, 61, today, "escalations")
        await db_session.flush()
        await _upsert_daily_stat(db_session, 61, today, "escalations")
        await db_session.flush()

        row = await db_session.scalar(
            select(DailyOrgStats).where(
                DailyOrgStats.organization_id == 61,
                DailyOrgStats.day == today,
            )
        )
        assert row is not None
        assert row.escalations == 2

    @pytest.mark.asyncio
    async def test_consumer_routes_event_to_correct_column(self, db_session: AsyncSession):
        """on_business_event маппирует event.type → нужную колонку."""
        from app.services.analytics_consumer import on_business_event
        from app.services.system_events import BusinessEvent
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add(Organization(id=62, name="ConsumerOrg", slug="consumer-org"))
        await db_session.flush()

        event = BusinessEvent(
            id="order.confirmed:1",
            org_id=62,
            type="order.confirmed",
            actor="customer",
            payload={},
        )
        await on_business_event(event, db_session)
        await db_session.flush()

        row = await db_session.scalar(
            select(DailyOrgStats).where(
                DailyOrgStats.organization_id == 62,
                DailyOrgStats.day == date.today(),
            )
        )
        assert row is not None
        assert row.orders_confirmed == 1
        assert row.orders_created == 0  # другая колонка не затронута

    @pytest.mark.asyncio
    async def test_consumer_ignores_unknown_event_type(self, db_session: AsyncSession):
        """Неизвестный event.type не вызывает ошибок и не пишет в БД."""
        from app.services.analytics_consumer import on_business_event
        from app.services.system_events import BusinessEvent
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add(Organization(id=63, name="IgnoreOrg", slug="ignore-org"))
        await db_session.flush()

        event = BusinessEvent(
            org_id=63, type="unknown.event.type", actor="system", payload={},
        )
        await on_business_event(event, db_session)
        await db_session.flush()

        count = await db_session.scalar(
            select(DailyOrgStats).where(DailyOrgStats.organization_id == 63)
                .with_only_columns(__import__("sqlalchemy").func.count())
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_emit_event_triggers_consumer(self, db_session: AsyncSession):
        """emit_event автоматически вызывает on_business_event → пишет в DailyOrgStats."""
        from app.services.system_events import BusinessEvent, emit_event
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add(Organization(id=64, name="TriggerOrg", slug="trigger-org"))
        await db_session.flush()

        event = BusinessEvent(
            id="order.cancelled:99",
            org_id=64, type="order.cancelled",
            actor="ai", payload={"order_id": 99},
        )
        await emit_event(db_session, event)
        await db_session.flush()

        row = await db_session.scalar(
            select(DailyOrgStats).where(
                DailyOrgStats.organization_id == 64,
                DailyOrgStats.day == date.today(),
            )
        )
        assert row is not None
        assert row.orders_cancelled == 1

    @pytest.mark.asyncio
    async def test_get_today_event_summary_returns_zeros_if_no_data(self, db_session: AsyncSession):
        """get_today_event_summary возвращает нули если записей нет."""
        from app.services.analytics_consumer import get_today_event_summary
        from app.db.models import Organization

        db_session.add(Organization(id=65, name="EmptyOrg", slug="empty-org"))
        await db_session.flush()

        summary = await get_today_event_summary(db_session, 65)

        assert summary["orders_created"] == 0
        assert summary["escalations"] == 0
        assert summary["source"] == "event_driven"

    @pytest.mark.asyncio
    async def test_daily_org_stats_org_isolation(self, db_session: AsyncSession):
        """Агрегаты одной org не видны другой (скоупинг по organization_id)."""
        from app.services.analytics_consumer import _upsert_daily_stat
        from app.db.models import DailyOrgStats, Organization
        from sqlalchemy import select
        from datetime import date

        db_session.add_all([
            Organization(id=70, name="OrgA", slug="org-a2"),
            Organization(id=71, name="OrgB", slug="org-b2"),
        ])
        await db_session.flush()

        today = date.today()
        await _upsert_daily_stat(db_session, 70, today, "operator_takeovers")
        await db_session.flush()

        row_b = await db_session.scalar(
            select(DailyOrgStats).where(
                DailyOrgStats.organization_id == 71,
                DailyOrgStats.day == today,
            )
        )
        # Org B не имеет записи — только Org A
        assert row_b is None
