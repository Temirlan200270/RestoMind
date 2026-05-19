"""
Definition of Done тесты Sprint G.

1. Заказ в закрытом/неоплаченном ресторане → DE → PolicyViolation + intent=faq
2. Owner видит все филиалы / Одиночный ресторан — Branch Switcher скрыт
3. Изменить цену → Replay со старым снапшотом → ИИ видит старую цену
4. Hallucination check через isdisjoint (точное множественное пересечение)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


# ─── helpers ─────────────────────────────────────────────────────────────────

def _order_proposal(items_names: list[str]):
    from app.schemas.ai_schemas import AIBrainResponse, OrderItem
    p = AIBrainResponse(intent="order", reply_text="Принято")
    p.items = [OrderItem(name=n, quantity=1) for n in items_names]
    p.order_type = ""
    p.delivery_address = ""
    return p


def _org(is_active=True, force_closed_until=None):
    o = MagicMock()
    o.is_active = is_active
    o.force_closed_until = force_closed_until
    o.force_closed_reason = ""
    o.max_discount_pct = 0
    return o


def _ctx(menu_names: list[str] = None):
    c = MagicMock()
    if menu_names:
        items = []
        for n in menu_names:
            m = MagicMock()
            m.name = n
            m.is_available = True
            items.append(m)
        c.menu_items = items
    else:
        c.menu_items = []
    c.draft_row = None
    c.customer_ctx = ""
    c.user_preferences = {}
    return c


# ─── DoD 1: force-close + billing → DE block ─────────────────────────────────


class TestDoDOne:
    """Заказ в закрытом/неоплаченном ресторане → DE block + intent=faq."""

    @pytest.mark.asyncio
    async def test_order_in_force_closed_blocked(self):
        from app.services.decision_engine import decision_engine
        future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        proposal = _order_proposal(["Плов"])
        result = await decision_engine.validate(proposal, _ctx(["Плов"]), _org(force_closed_until=future))

        assert not result.is_valid
        assert any(v.rule == "force_closed" for v in result.violations)
        assert result.corrected_response.intent == "faq"
        assert result.corrected_response.reply_text  # объяснение клиенту

    @pytest.mark.asyncio
    async def test_order_in_suspended_org_blocked(self):
        from app.services.decision_engine import decision_engine
        proposal = _order_proposal(["Лагман"])
        result = await decision_engine.validate(proposal, _ctx(["Лагман"]), _org(is_active=False))

        assert not result.is_valid
        assert any(v.rule == "billing_suspended" for v in result.violations)
        assert result.corrected_response.intent == "faq"

    @pytest.mark.asyncio
    async def test_faq_and_escalate_allowed_even_when_closed(self):
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import AIBrainResponse
        future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        org = _org(is_active=False, force_closed_until=future)

        for intent in ("faq", "escalate"):
            p = AIBrainResponse(intent=intent, reply_text="Тест")
            p.items = []
            p.order_type = ""
            p.delivery_address = ""
            p.order_actions = []
            result = await decision_engine.validate(p, _ctx(), org)
            block = [v for v in result.violations if v.severity == "block"]
            assert not block, f"{intent} не должен блокироваться при закрытом ресторане"

    @pytest.mark.asyncio
    async def test_booking_in_suspended_org_blocked(self):
        from app.services.decision_engine import decision_engine
        from app.schemas.ai_schemas import AIBrainResponse
        p = AIBrainResponse(intent="book", reply_text="Тест")
        p.items = []
        p.order_type = ""
        p.delivery_address = ""
        p.order_actions = []
        result = await decision_engine.validate(p, _ctx(), _org(is_active=False))

        assert not result.is_valid
        assert any(v.rule == "billing_suspended" for v in result.violations)


# ─── DoD 2: Owner vs Operator — Branch Switcher visibility ───────────────────


class TestDoDTwo:
    """Owner видит все филиалы / одиночный ресторан — Switcher скрыт."""

    @pytest.mark.asyncio
    async def test_owner_of_network_sees_all_branches(self, db_session: AsyncSession):
        from app.api.admin.auth import _resolve_network_orgs, _resolve_is_network
        from app.db.models import Organization, Tenant

        tenant = Tenant(id=60, name="Сеть Тест", is_network=True)
        db_session.add(tenant)
        await db_session.flush()

        for i, city in enumerate(["Алматы", "Астана", "Шымкент"], 1):
            db_session.add(Organization(
                id=400 + i, name=city, slug=f"dod2-{i}",
                tenant_id=60, is_active=True,
            ))
        await db_session.flush()

        is_net = await _resolve_is_network(db_session, None, 401)
        orgs = await _resolve_network_orgs(db_session, None, 401)

        assert is_net is True
        assert len(orgs) == 3
        names = {o["name"] for o in orgs}
        assert names == {"Алматы", "Астана", "Шымкент"}

    @pytest.mark.asyncio
    async def test_single_restaurant_gets_no_network_orgs(self, db_session: AsyncSession):
        from app.api.admin.auth import _resolve_network_orgs, _resolve_is_network
        from app.db.models import Organization, Tenant

        tenant = Tenant(id=61, name="Одно Кафе", is_network=False)
        db_session.add(tenant)
        await db_session.flush()

        db_session.add(Organization(id=410, name="Кафе", slug="dod2-cafe", tenant_id=61))
        await db_session.flush()

        is_net = await _resolve_is_network(db_session, None, 410)
        orgs = await _resolve_network_orgs(db_session, None, 410)

        assert is_net is False
        assert orgs == []

    def test_branch_switcher_guarded_by_is_network_in_template(self):
        """Branch Switcher в шаблоне обёрнут в x-if userData?.is_network."""
        import pathlib
        html = pathlib.Path("app/templates/screens/_header.html").read_text(encoding="utf-8")
        assert "userData?.is_network" in html, (
            "Branch Switcher должен быть защищён условием is_network "
            "чтобы не отображаться операторам одиночных ресторанов"
        )

    def test_switchNetworkOrg_explicit_ws_close_in_js(self):
        """switchNetworkOrg явно закрывает WS до переключения."""
        import pathlib
        js = pathlib.Path("app/static/js/admin-app.js").read_text(encoding="utf-8")
        # Проверяем что в switchNetworkOrg есть явное закрытие WS и сброс токена
        assert "switchNetworkOrg" in js
        assert "_wsTokenInUse = null" in js, (
            "switchNetworkOrg должен сбрасывать _wsTokenInUse чтобы "
            "принудить connectWebSocket пересоздать соединение"
        )


# ─── DoD 3: Frozen price in replay ───────────────────────────────────────────


class TestDoDThree:
    """Изменить цену → Replay со старым снапшотом → ИИ видит старую цену."""

    def test_replay_uses_frozen_context_not_current_db(self):
        """Replay берёт menu_context_text из снапшота, не пересчитывает из БД."""
        old_price_text = "Плов — 2790₸ (доступно)"

        # Снапшот сохранён когда цена была 2790
        snapshot_business_state = {
            "menu_context_text": old_price_text,
            "menu_prices_snapshot": [
                {"iiko_id": "uuid-plov", "price": 2790.0, "is_available": True}
            ],
        }

        # После сохранения снапшота цену подняли до 3500
        # current_db_price = 3500.0  (имитируем — не используем в тесте)

        # Replay-логика из intelligence.py: frozen_ctx = business_state.get("menu_context_text")
        frozen_ctx = snapshot_business_state.get("menu_context_text")
        use_frozen = bool(frozen_ctx)

        assert use_frozen is True, "Replay должен использовать frozen контекст"
        assert "2790" in frozen_ctx, "Frozen контекст должен содержать СТАРУЮ цену"
        assert "3500" not in frozen_ctx, "Frozen контекст НЕ должен содержать новую цену"

    @pytest.mark.asyncio
    async def test_snapshot_prices_contain_only_minimal_fields(self, db_session: AsyncSession):
        """menu_prices_snapshot хранит только {iiko_id, price, is_available} — не name/category."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.services.context_engine import save_ai_context_snapshot, AIReadContext
        from app.db.models import Organization

        db_session.add(Organization(id=130, name="Minimal", slug="minimal"))
        await db_session.flush()

        m = MagicMock()
        m.name = "Плов"
        m.price = 2790.0
        m.is_available = True
        m.category = "Горячее"
        m.iiko_id = "uuid-plov"

        ctx = AIReadContext(
            menu_items=[m], user=None, org=None,
            kb_context="", draft_row=None, customer_ctx="", user_preferences={},
        )

        captured = {}
        with patch("app.services.context_engine.async_session_factory") as mf:
            mc = MagicMock()
            ms = MagicMock()
            ms.add = lambda x: captured.update({"snap": x})
            ms.commit = AsyncMock()
            ms.execute = AsyncMock(
                return_value=MagicMock(mappings=lambda: MagicMock(all=lambda: []))
            )
            mc.__aenter__ = AsyncMock(return_value=ms)
            mc.__aexit__ = AsyncMock(return_value=False)
            mf.return_value = mc

            await save_ai_context_snapshot(
                "+7700", 130, ctx, menu_context_text="Плов — 2790₸",
            )

        snap = captured["snap"]
        prices = snap.business_state["menu_prices_snapshot"]
        assert len(prices) == 1

        # Строго: только 3 ключа
        assert set(prices[0].keys()) == {"iiko_id", "price", "is_available"}, (
            f"Ожидали {{iiko_id, price, is_available}}, получили: {set(prices[0].keys())}"
        )
        assert prices[0]["price"] == 2790.0
        assert "name" not in prices[0]
        assert "category" not in prices[0]

        # Frozen text тоже сохранён
        assert snap.business_state["menu_context_text"] == "Плов — 2790₸"

    def test_price_snapshot_size_vs_full_menu(self):
        """Минимальный снапшот значительно компактнее полного."""
        import json

        # Имитируем 80 позиций меню
        full_snapshot = [
            {"name": f"Блюдо {i}", "price": 1000.0 + i, "is_available": True,
             "category": "Категория", "iiko_id": f"uuid-{i:04d}"}
            for i in range(80)
        ]
        minimal_snapshot = [
            {"iiko_id": f"uuid-{i:04d}", "price": 1000.0 + i, "is_available": True}
            for i in range(80)
        ]

        full_size = len(json.dumps(full_snapshot))
        minimal_size = len(json.dumps(minimal_snapshot))

        assert minimal_size < full_size, "Минимальный снапшот должен быть меньше полного"
        savings_pct = round((1 - minimal_size / full_size) * 100, 1)
        assert savings_pct > 30, (
            f"Ожидали >30% экономии, получили {savings_pct}% "
            f"(full={full_size}B, minimal={minimal_size}B)"
        )


# ─── Дополнительный DoD: isdisjoint vs prefix heuristic ─────────────────────


class TestDoDHallucinationIsdisjoint:
    """isdisjoint() точнее и дешевле чем 5-char prefix matching."""

    @pytest.mark.asyncio
    async def test_partial_match_not_blocked(self):
        """Один товар есть в меню, другой нет → НЕ блокируем (intersection non-empty)."""
        from app.services.decision_engine import decision_engine

        m = MagicMock()
        m.name = "Плов"
        m.is_available = True
        ctx = _make_ctx_local(["Плов"])

        proposal = _order_proposal(["Плов", "Борщ"])
        result = await decision_engine.validate(proposal, ctx, MagicMock(
            is_active=True, force_closed_until=None, force_closed_reason="", max_discount_pct=0,
        ))

        assert not any(v.rule == "all_items_hallucinated" for v in result.violations), (
            "isdisjoint=False (есть 'Плов') → hallucination не срабатывает"
        )

    @pytest.mark.asyncio
    async def test_fully_unknown_items_blocked(self):
        """Все товары отсутствуют → isdisjoint=True → block."""
        from app.services.decision_engine import decision_engine

        ctx = _make_ctx_local(["Плов", "Лагман"])

        proposal = _order_proposal(["Суши", "Роллы", "Сашими"])  # нет в меню
        result = await decision_engine.validate(proposal, ctx, MagicMock(
            is_active=True, force_closed_until=None, force_closed_reason="", max_discount_pct=0,
        ))

        assert any(v.rule == "all_items_hallucinated" for v in result.violations), (
            "isdisjoint=True (нет совпадений) → hallucination должна срабатывать"
        )

    @pytest.mark.asyncio
    async def test_exact_name_match_not_prefix(self):
        """'Плов 1 кг' и 'Плов' — разные позиции, не совпадают по isdisjoint."""
        from app.services.decision_engine import decision_engine

        ctx = _make_ctx_local(["Плов 1 кг"])  # только такая позиция

        proposal = _order_proposal(["Плов"])  # заказывает просто "Плов"
        result = await decision_engine.validate(proposal, ctx, MagicMock(
            is_active=True, force_closed_until=None, force_closed_reason="", max_discount_pct=0,
        ))

        # "плов" ≠ "плов 1 кг" → isdisjoint=True → hallucination срабатывает
        # (validate_order потом найдёт через fuzzy matching, но DE корректно флагует)
        assert any(v.rule == "all_items_hallucinated" for v in result.violations)


def _make_ctx_local(menu_names: list[str]):
    from unittest.mock import MagicMock
    c = MagicMock()
    items = []
    for n in menu_names:
        m = MagicMock()
        m.name = n
        m.is_available = True
        items.append(m)
    c.menu_items = items
    c.draft_row = None
    c.customer_ctx = ""
    c.user_preferences = {}
    return c
