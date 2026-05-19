"""
Sprint G: Decision Engine 95% + Franchise Phase 1 OS + AI Snapshot 80%.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from sqlalchemy.ext.asyncio import AsyncSession


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_proposal(intent="order", items=None, order_type="", delivery_address=""):
    from app.schemas.ai_schemas import AIBrainResponse
    p = AIBrainResponse(intent=intent, reply_text="Тест")
    if items is not None:
        p.items = items
    p.order_type = order_type
    p.delivery_address = delivery_address
    return p


def _make_org(is_active=True, force_closed_until=None, max_discount_pct=0):
    o = MagicMock()
    o.is_active = is_active
    o.force_closed_until = force_closed_until
    o.force_closed_reason = ""
    o.max_discount_pct = max_discount_pct
    return o


def _make_tenant(plan_status="active", is_network=False):
    t = MagicMock()
    t.plan_status = plan_status
    t.is_network = is_network
    return t


def _make_ctx(menu_items=None):
    c = MagicMock()
    c.menu_items = menu_items or []
    c.draft_row = None
    c.customer_ctx = ""
    c.user_preferences = {}
    return c


# ─── G1: Decision Engine → 95% ───────────────────────────────────────────────


class TestDecisionEngineG1:
    """G1: billing_suspended + hallucination check + pricing."""

    @pytest.mark.asyncio
    async def test_billing_suspended_tenant_blocks_order(self):
        from app.services.decision_engine import decision_engine
        proposal = _make_proposal("order")
        ctx = _make_ctx()
        org = _make_org()
        tenant = _make_tenant(plan_status="suspended")

        result = await decision_engine.validate(proposal, ctx, org, tenant=tenant)

        assert not result.is_valid
        assert any(v.rule == "billing_suspended" for v in result.violations)
        assert result.corrected_response.intent == "faq"

    @pytest.mark.asyncio
    async def test_billing_suspended_via_flag_blocks_order(self):
        from app.services.decision_engine import decision_engine
        proposal = _make_proposal("order")
        ctx = _make_ctx()
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org, billing_suspended=True)

        assert not result.is_valid
        assert any(v.rule == "billing_suspended" for v in result.violations)

    @pytest.mark.asyncio
    async def test_billing_suspended_via_org_is_active_false(self):
        from app.services.decision_engine import decision_engine
        proposal = _make_proposal("order")
        ctx = _make_ctx()
        org = _make_org(is_active=False)

        result = await decision_engine.validate(proposal, ctx, org)

        assert not result.is_valid
        assert any(v.rule == "billing_suspended" for v in result.violations)

    @pytest.mark.asyncio
    async def test_billing_suspended_allows_faq(self):
        from app.services.decision_engine import decision_engine
        proposal = _make_proposal("faq")
        ctx = _make_ctx()
        org = _make_org(is_active=False)

        result = await decision_engine.validate(proposal, ctx, org)

        assert not any(v.rule == "billing_suspended" for v in result.violations)

    @pytest.mark.asyncio
    async def test_billing_suspended_blocks_booking_too(self):
        from app.services.decision_engine import decision_engine
        proposal = _make_proposal("book")
        ctx = _make_ctx()
        org = _make_org(is_active=False)

        result = await decision_engine.validate(proposal, ctx, org)

        assert not result.is_valid
        assert any(v.rule == "billing_suspended" for v in result.violations)

    @pytest.mark.asyncio
    async def test_active_billing_no_violation(self):
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import OrderItem
        proposal = _make_proposal("order")
        proposal.items = [OrderItem(name="Плов", quantity=1)]
        ctx = _make_ctx()
        org = _make_org(is_active=True)
        tenant = _make_tenant(plan_status="active")

        result = await decision_engine.validate(proposal, ctx, org, tenant=tenant)

        assert not any(v.rule == "billing_suspended" for v in result.violations)

    @pytest.mark.asyncio
    async def test_all_items_hallucinated_blocks_when_menu_known(self):
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import OrderItem

        menu_item = MagicMock()
        menu_item.name = "Плов"
        menu_item.is_available = True

        proposal = _make_proposal("order")
        proposal.items = [
            OrderItem(name="Жареный дракон", quantity=1),
            OrderItem(name="Суп из единорога", quantity=2),
        ]
        ctx = _make_ctx(menu_items=[menu_item])
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert not result.is_valid
        assert any(v.rule == "all_items_hallucinated" for v in result.violations)
        assert result.corrected_response.intent == "faq"

    @pytest.mark.asyncio
    async def test_partial_unknown_items_not_blocked(self):
        """Если хотя бы одна позиция найдена в меню — не блокируем."""
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import OrderItem

        menu_item = MagicMock()
        menu_item.name = "Плов"
        menu_item.is_available = True

        proposal = _make_proposal("order")
        proposal.items = [
            OrderItem(name="Плов", quantity=1),      # есть в меню
            OrderItem(name="Ракета", quantity=1),    # нет в меню
        ]
        ctx = _make_ctx(menu_items=[menu_item])
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert not any(v.rule == "all_items_hallucinated" for v in result.violations)

    @pytest.mark.asyncio
    async def test_empty_menu_no_hallucination_check(self):
        """Если меню не загружено — проверка на галлюцинации пропускается."""
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import OrderItem

        proposal = _make_proposal("order")
        proposal.items = [OrderItem(name="Что угодно", quantity=1)]
        ctx = _make_ctx(menu_items=[])  # меню пустое
        org = _make_org()

        result = await decision_engine.validate(proposal, ctx, org)

        assert not any(v.rule == "all_items_hallucinated" for v in result.violations)

    @pytest.mark.asyncio
    async def test_discount_exceeds_policy_blocks(self):
        """discount_pct > max_discount_pct → block (Phase 4.2)."""
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import OrderItem

        proposal = _make_proposal("order")
        proposal.items = [OrderItem(name="Плов", quantity=1)]
        proposal.discount_pct = 30  # AI предлагает 30%

        org = _make_org(max_discount_pct=15)
        ctx = _make_ctx()

        result = await decision_engine.validate(proposal, ctx, org)

        assert not result.is_valid
        assert any(v.rule == "discount_exceeds_policy" for v in result.violations)


# ─── G2: Franchise Phase 1 OS ────────────────────────────────────────────────


class TestFranchisePhase1:
    """G2: Tenant.is_network field, /network/* endpoints isolation."""

    @pytest.mark.asyncio
    async def test_tenant_is_network_field_exists(self, db_session: AsyncSession):
        """Tenant.is_network сохраняется в БД."""
        from app.db.models import Tenant
        from sqlalchemy import select

        t = Tenant(name="Test Network", plan="standard", is_network=True)
        db_session.add(t)
        await db_session.flush()

        row = await db_session.scalar(select(Tenant).where(Tenant.id == t.id))
        assert row is not None
        assert row.is_network is True

    @pytest.mark.asyncio
    async def test_is_network_defaults_to_false(self, db_session: AsyncSession):
        """По умолчанию is_network=False для новых тенантов."""
        from app.db.models import Tenant
        from sqlalchemy import select

        t = Tenant(name="Single Cafe")
        db_session.add(t)
        await db_session.flush()

        row = await db_session.scalar(select(Tenant).where(Tenant.id == t.id))
        assert row is not None
        assert bool(row.is_network) is False

    @pytest.mark.asyncio
    async def test_resolve_is_network_false_for_no_tenant(self, db_session: AsyncSession):
        """_resolve_is_network возвращает False если у org нет tenant_id."""
        from app.api.admin.auth import _resolve_is_network
        from app.db.models import Organization

        org = Organization(id=200, name="Solo", slug="solo")
        db_session.add(org)
        await db_session.flush()

        result = await _resolve_is_network(db_session, None, 200)
        assert result is False

    @pytest.mark.asyncio
    async def test_resolve_is_network_true_for_network_tenant(self, db_session: AsyncSession):
        """_resolve_is_network возвращает True если Tenant.is_network=True."""
        from app.api.admin.auth import _resolve_is_network
        from app.db.models import Organization, Tenant

        tenant = Tenant(id=30, name="Network", is_network=True)
        db_session.add(tenant)
        await db_session.flush()

        org = Organization(id=201, name="Branch1", slug="branch1", tenant_id=30)
        db_session.add(org)
        await db_session.flush()

        result = await _resolve_is_network(db_session, None, 201)
        assert result is True

    @pytest.mark.asyncio
    async def test_resolve_network_orgs_empty_for_single(self, db_session: AsyncSession):
        """_resolve_network_orgs возвращает [] если is_network=False."""
        from app.api.admin.auth import _resolve_network_orgs
        from app.db.models import Organization, Tenant

        tenant = Tenant(id=31, name="Solo", is_network=False)
        db_session.add(tenant)
        await db_session.flush()

        org = Organization(id=202, name="Cafe", slug="cafe", tenant_id=31)
        db_session.add(org)
        await db_session.flush()

        result = await _resolve_network_orgs(db_session, None, 202)
        assert result == []

    @pytest.mark.asyncio
    async def test_resolve_network_orgs_lists_all_branches(self, db_session: AsyncSession):
        """_resolve_network_orgs возвращает все активные филиалы сети."""
        from app.api.admin.auth import _resolve_network_orgs
        from app.db.models import Organization, Tenant

        tenant = Tenant(id=32, name="Chain", is_network=True)
        db_session.add(tenant)
        await db_session.flush()

        for i, name in enumerate(["Branch A", "Branch B", "Branch C"], start=1):
            db_session.add(Organization(
                id=210 + i, name=name, slug=f"branch-{i}",
                tenant_id=32, is_active=True,
            ))
        await db_session.flush()

        result = await _resolve_network_orgs(db_session, None, 211)
        assert len(result) == 3
        names = [r["name"] for r in result]
        assert "Branch A" in names
        assert "Branch B" in names


# ─── G3: AI Snapshot → 80% ───────────────────────────────────────────────────


class TestSnapshotG3:
    """G3: menu_prices_snapshot + menu_context_text + frozen replay."""

    @pytest.mark.asyncio
    async def test_snapshot_stores_full_menu_prices(self, db_session: AsyncSession):
        """menu_prices_snapshot содержит все позиции меню с ценами."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.services.context_engine import save_ai_context_snapshot, AIReadContext
        from app.db.models import Organization

        db_session.add(Organization(id=110, name="SnapG3", slug="snap-g3"))
        await db_session.flush()

        menu_item = MagicMock()
        menu_item.name = "Плов"
        menu_item.price = 2790.0
        menu_item.is_available = True
        menu_item.category = "Горячее"
        menu_item.iiko_id = "uuid-plov"

        ctx = AIReadContext(
            menu_items=[menu_item],
            user=None, org=None, kb_context="",
            draft_row=None, customer_ctx="", user_preferences={},
        )

        captured_snap = {}

        with patch("app.services.context_engine.async_session_factory") as mock_factory:
            mock_cm = MagicMock()
            mock_session = MagicMock()
            mock_session.add = lambda x: captured_snap.update({"snap": x})
            mock_session.commit = AsyncMock()
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.execute = AsyncMock(return_value=MagicMock(mappings=lambda: MagicMock(all=lambda: [])))
            mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_cm

            snap_id = await save_ai_context_snapshot(
                "+7700", 110, ctx,
                menu_context_text="Меню: Плов 2790₸",
            )

        snap = captured_snap.get("snap")
        assert snap is not None
        bs = snap.business_state
        assert "menu_prices_snapshot" in bs
        assert len(bs["menu_prices_snapshot"]) == 1
        assert bs["menu_prices_snapshot"][0]["iiko_id"] == "uuid-plov"
        assert bs["menu_prices_snapshot"][0]["price"] == 2790.0
        assert bs["menu_prices_snapshot"][0]["is_available"] is True
        assert "name" not in bs["menu_prices_snapshot"][0]
        assert bs["menu_context_text"] == "Меню: Плов 2790₸"

    @pytest.mark.asyncio
    async def test_snapshot_stores_all_items_not_just_40(self, db_session: AsyncSession):
        """menu_prices_snapshot содержит все позиции, не ограниченные 40."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.services.context_engine import save_ai_context_snapshot, AIReadContext
        from app.db.models import Organization

        db_session.add(Organization(id=111, name="BigMenu", slug="big-menu"))
        await db_session.flush()

        menu_items = []
        for i in range(60):
            m = MagicMock()
            m.name = f"Блюдо {i}"
            m.price = float(1000 + i)
            m.is_available = True
            m.category = "Тест"
            m.iiko_id = f"uuid-{i}"
            menu_items.append(m)

        ctx = AIReadContext(
            menu_items=menu_items,
            user=None, org=None, kb_context="",
            draft_row=None, customer_ctx="", user_preferences={},
        )

        captured_snap = {}

        with patch("app.services.context_engine.async_session_factory") as mock_factory:
            mock_cm = MagicMock()
            mock_session = MagicMock()
            mock_session.add = lambda x: captured_snap.update({"snap": x})
            mock_session.commit = AsyncMock()
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.execute = AsyncMock(return_value=MagicMock(mappings=lambda: MagicMock(all=lambda: [])))
            mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_cm

            await save_ai_context_snapshot("+7700", 111, ctx)

        snap = captured_snap.get("snap")
        assert snap is not None
        bs = snap.business_state
        assert len(bs["menu_prices_snapshot"]) == 60  # все 60, не только 40

    def test_snapshot_has_menu_context_text_none_if_not_passed(self):
        """Если menu_context_text не передан — None в business_state."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.services.context_engine import save_ai_context_snapshot, AIReadContext

        ctx = AIReadContext(
            menu_items=[], user=None, org=None, kb_context="",
            draft_row=None, customer_ctx="", user_preferences={},
        )

        captured = {}

        with patch("app.services.context_engine.async_session_factory") as mock_factory:
            mock_cm = MagicMock()
            mock_session = MagicMock()
            mock_session.add = lambda x: captured.update({"snap": x})
            mock_session.commit = AsyncMock()
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.execute = AsyncMock(return_value=MagicMock(mappings=lambda: MagicMock(all=lambda: [])))
            mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_cm

            asyncio.run(
                save_ai_context_snapshot("+7700", 1, ctx)
            )

        snap = captured.get("snap")
        assert snap is not None
        # menu_context_text=None — в поле None, но ключ присутствует
        assert snap.business_state["menu_context_text"] is None
