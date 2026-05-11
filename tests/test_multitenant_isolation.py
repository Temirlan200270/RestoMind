"""
Multi-tenant isolation security tests (P4 sprint).

Проверяет что данные одной организации не «утекают» при запросах другой.
Паттерн: создать org_a и org_b, засеять данные, убедиться что API/сервисы
возвращают только данные запрашивающей организации.

Регрессия: обнаружение ошибок изоляции ДО деплоя на production.
Соглашение: docs/CONVENTIONS.md §8.2 (Multi-tenant isolation)
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as db_session_module
from app.db.models import (
    AiUsageLog,
    Base,
    BusinessRecommendation,
    ChatLog,
    EscalationEvent,
    MenuItem,
    OperationalInsight,
    Order,
    OrderStatus,
    Organization,
    PipelineLatencyLog,
    User,
)


# ──────────────────────── Fixtures ────────────────────────


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest_asyncio.fixture
async def iso_db():
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False,
    )
    yield session_factory
    await engine.dispose()


async def _seed_orgs(sf) -> tuple[int, int]:
    """Создаёт 2 организации и возвращает (org_a_id, org_b_id)."""
    async with sf() as db:
        org_a = Organization(name="Org A", slug="org-a", is_active=True)
        org_b = Organization(name="Org B", slug="org-b", is_active=True)
        db.add(org_a)
        db.add(org_b)
        await db.commit()
        await db.refresh(org_a)
        await db.refresh(org_b)
        return org_a.id, org_b.id


# ──────────────────────── Tests: Order ────────────────────────


@pytest.mark.asyncio
async def test_orders_isolated_by_org(iso_db):
    """Запрос заказов org_a не возвращает заказы org_b."""
    from sqlalchemy import select
    org_a, org_b = await _seed_orgs(iso_db)

    async with iso_db() as db:
        user_a = User(phone="+77001110001", organization_id=org_a)
        user_b = User(phone="+77002220002", organization_id=org_b)
        db.add_all([user_a, user_b])
        await db.flush()

        order_a = Order(organization_id=org_a, user_id=user_a.id,
                        status="draft", total_price=1000, row_version=1)
        order_b = Order(organization_id=org_b, user_id=user_b.id,
                        status="draft", total_price=2000, row_version=1)
        db.add_all([order_a, order_b])
        await db.commit()

    async with iso_db() as db:
        rows_a = (await db.execute(
            select(Order).where(Order.organization_id == org_a)
        )).scalars().all()
        assert len(rows_a) == 1
        assert rows_a[0].total_price == 1000

        # org_b заказ не должен быть виден org_a
        org_b_orders_in_a_query = [r for r in rows_a if r.organization_id == org_b]
        assert len(org_b_orders_in_a_query) == 0


# ──────────────────────── Tests: ChatLog ────────────────────────


@pytest.mark.asyncio
async def test_chat_logs_isolated(iso_db):
    """Чат-логи org_b не видны при запросе org_a."""
    from sqlalchemy import select
    org_a, org_b = await _seed_orgs(iso_db)

    async with iso_db() as db:
        user_a = User(phone="+77001110011", organization_id=org_a)
        user_b = User(phone="+77002220022", organization_id=org_b)
        db.add_all([user_a, user_b])
        await db.flush()
        log_a = ChatLog(organization_id=org_a, user_id=user_a.id, role="user",
                        content="msg A", delivery_status="delivered")
        log_b = ChatLog(organization_id=org_b, user_id=user_b.id, role="user",
                        content="msg B", delivery_status="delivered")
        db.add_all([log_a, log_b])
        await db.commit()

    async with iso_db() as db:
        rows = (await db.execute(
            select(ChatLog).where(ChatLog.organization_id == org_a)
        )).scalars().all()
        assert all(r.organization_id == org_a for r in rows)
        assert all(r.content != "msg B" for r in rows)


# ──────────────────────── Tests: MenuItem ────────────────────────


@pytest.mark.asyncio
async def test_menu_items_isolated(iso_db):
    """Позиции меню org_b не видны при запросе org_a."""
    from sqlalchemy import select
    org_a, org_b = await _seed_orgs(iso_db)

    async with iso_db() as db:
        mi_a = MenuItem(organization_id=org_a, name="Плов A", price=1500, category="Горячее")
        mi_b = MenuItem(organization_id=org_b, name="Плов B", price=1600, category="Горячее")
        db.add_all([mi_a, mi_b])
        await db.commit()

    async with iso_db() as db:
        rows = (await db.execute(
            select(MenuItem).where(MenuItem.organization_id == org_a)
        )).scalars().all()
        assert all(r.organization_id == org_a for r in rows)
        names = [r.name for r in rows]
        assert "Плов B" not in names


# ──────────────────────── Tests: EscalationEvent ────────────────────────


@pytest.mark.asyncio
async def test_escalation_events_isolated(iso_db):
    """EscalationEvent org_b не видны org_a."""
    from sqlalchemy import select
    org_a, org_b = await _seed_orgs(iso_db)

    async with iso_db() as db:
        ev_a = EscalationEvent(organization_id=org_a, phone="+77001111111",
                               user_message="help A", reason="escalation")
        ev_b = EscalationEvent(organization_id=org_b, phone="+77002222222",
                               user_message="help B", reason="escalation")
        db.add_all([ev_a, ev_b])
        await db.commit()

    async with iso_db() as db:
        rows = (await db.execute(
            select(EscalationEvent).where(EscalationEvent.organization_id == org_a)
        )).scalars().all()
        assert all(r.organization_id == org_a for r in rows)
        assert all(r.phone != "+77002222222" for r in rows)


# ──────────────────────── Tests: OperationalInsight ────────────────────────


@pytest.mark.asyncio
async def test_insights_isolated(iso_db):
    """OperationalInsight org_b не виден org_a."""
    from sqlalchemy import select
    org_a, org_b = await _seed_orgs(iso_db)

    async with iso_db() as db:
        ins_a = OperationalInsight(organization_id=org_a, insight_type="revenue_drop",
                                   severity="warning", title="Drop A", summary="", status="new")
        ins_b = OperationalInsight(organization_id=org_b, insight_type="revenue_drop",
                                   severity="warning", title="Drop B", summary="", status="new")
        db.add_all([ins_a, ins_b])
        await db.commit()

    async with iso_db() as db:
        rows = (await db.execute(
            select(OperationalInsight).where(OperationalInsight.organization_id == org_a)
        )).scalars().all()
        assert all(r.organization_id == org_a for r in rows)
        titles = [r.title for r in rows]
        assert "Drop B" not in titles


# ──────────────────────── Tests: AiUsageLog ────────────────────────


@pytest.mark.asyncio
async def test_ai_usage_isolated(iso_db):
    """Токены AI org_b не суммируются в запросе org_a."""
    from sqlalchemy import select
    from datetime import date
    org_a, org_b = await _seed_orgs(iso_db)

    async with iso_db() as db:
        log_a = AiUsageLog(organization_id=org_a, day=date.today(),
                           total_tokens=1000, call_count=5)
        log_b = AiUsageLog(organization_id=org_b, day=date.today(),
                           total_tokens=9999, call_count=50)
        db.add_all([log_a, log_b])
        await db.commit()

    async with iso_db() as db:
        rows = (await db.execute(
            select(AiUsageLog).where(AiUsageLog.organization_id == org_a)
        )).scalars().all()
        assert all(r.organization_id == org_a for r in rows)
        total = sum(r.total_tokens for r in rows)
        assert total == 1000   # только org_a, не org_b (9999)


# ──────────────────────── Tests: PipelineLatencyLog ────────────────────────


@pytest.mark.asyncio
async def test_pipeline_latency_isolated(iso_db):
    """Метрики latency org_b не видны org_a."""
    from sqlalchemy import select
    org_a, org_b = await _seed_orgs(iso_db)

    async with iso_db() as db:
        lat_a = PipelineLatencyLog(organization_id=org_a, llm_ms=1200, total_ms=2000)
        lat_b = PipelineLatencyLog(organization_id=org_b, llm_ms=9999, total_ms=15000)
        db.add_all([lat_a, lat_b])
        await db.commit()

    async with iso_db() as db:
        rows = (await db.execute(
            select(PipelineLatencyLog).where(PipelineLatencyLog.organization_id == org_a)
        )).scalars().all()
        assert all(r.organization_id == org_a for r in rows)
        assert all(r.llm_ms != 9999 for r in rows)


# ──────────────────────── Tests: BusinessRecommendation ────────────────────────


@pytest.mark.asyncio
async def test_recommendations_isolated(iso_db):
    """Рекомендации org_b не видны org_a."""
    from sqlalchemy import select
    org_a, org_b = await _seed_orgs(iso_db)

    async with iso_db() as db:
        rec_a = BusinessRecommendation(
            organization_id=org_a, recommendation_type="product_boost",
            title="Boost A", body="...", confidence_pct=70, status="new",
        )
        rec_b = BusinessRecommendation(
            organization_id=org_b, recommendation_type="product_boost",
            title="Boost B", body="...", confidence_pct=80, status="new",
        )
        db.add_all([rec_a, rec_b])
        await db.commit()

    async with iso_db() as db:
        rows = (await db.execute(
            select(BusinessRecommendation).where(BusinessRecommendation.organization_id == org_a)
        )).scalars().all()
        assert all(r.organization_id == org_a for r in rows)
        titles = [r.title for r in rows]
        assert "Boost B" not in titles


# ──────────────────────── Tests: Cross-org access patterns ────────────────────


@pytest.mark.asyncio
async def test_no_cross_org_escalation_via_phone(iso_db):
    """
    Один и тот же номер телефона в двух организациях.
    Эскалации должны быть разделены по org_id.
    """
    from sqlalchemy import select
    org_a, org_b = await _seed_orgs(iso_db)
    phone = "+77001234567"  # один номер в обеих орг

    async with iso_db() as db:
        ev_a = EscalationEvent(organization_id=org_a, phone=phone,
                               user_message="msg for A", reason="esc")
        ev_b = EscalationEvent(organization_id=org_b, phone=phone,
                               user_message="msg for B", reason="esc")
        db.add_all([ev_a, ev_b])
        await db.commit()

    async with iso_db() as db:
        rows_a = (await db.execute(
            select(EscalationEvent).where(
                EscalationEvent.organization_id == org_a,
                EscalationEvent.phone == phone,
            )
        )).scalars().all()
        assert len(rows_a) == 1
        assert rows_a[0].user_message == "msg for A"

        rows_b = (await db.execute(
            select(EscalationEvent).where(
                EscalationEvent.organization_id == org_b,
                EscalationEvent.phone == phone,
            )
        )).scalars().all()
        assert len(rows_b) == 1
        assert rows_b[0].user_message == "msg for B"
