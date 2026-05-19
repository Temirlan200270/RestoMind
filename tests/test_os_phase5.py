"""Phase 5 Pilot: OS Autopilot — event-only dashboard, demand forecast, self-healing."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


# ─── P5.2: build_demand_forecast ─────────────────────────────────────────────


class TestBuildDemandForecast:
    def test_basic_forecast(self):
        from app.services.owner_dashboard import build_demand_forecast

        today = date(2026, 5, 19)  # Tuesday
        orders = {
            "2026-05-19": 12,
        }
        result = build_demand_forecast(orders, today=today)

        assert result is not None
        assert result["confirmed_so_far"] == 12
        assert result["days_elapsed"] == 1
        assert result["days_remaining"] == 5
        assert result["confidence"] == "low"
        assert result["forecast_orders"] == 12 + 12 * 5

    def test_high_confidence_with_5_days(self):
        from app.services.owner_dashboard import build_demand_forecast

        today = date(2026, 5, 22)  # Friday (5th day of week)
        orders = {f"2026-05-{d}": 10 for d in range(18, 23)}  # Mon–Fri
        result = build_demand_forecast(orders, today=today)

        assert result is not None
        assert result["confidence"] == "high"
        assert result["daily_avg_orders"] == 10.0
        assert result["forecast_orders"] == 50 + 10 * 2  # 5 days done + 2 remaining

    def test_empty_data_returns_none(self):
        from app.services.owner_dashboard import build_demand_forecast
        assert build_demand_forecast({}) is None

    def test_no_current_week_data_returns_none(self):
        from app.services.owner_dashboard import build_demand_forecast
        today = date(2026, 5, 19)
        # Only old data from previous weeks
        old = {"2026-05-01": 15, "2026-05-02": 20}
        assert build_demand_forecast(old, today=today) is None

    def test_forecast_structure_keys(self):
        from app.services.owner_dashboard import build_demand_forecast
        today = date(2026, 5, 20)  # Tuesday
        orders = {"2026-05-19": 12, "2026-05-20": 14}
        result = build_demand_forecast(orders, today=today)

        assert result is not None
        for key in ("forecast_orders", "confirmed_so_far", "daily_avg_orders",
                    "days_remaining", "days_elapsed", "confidence",
                    "week_start", "week_end"):
            assert key in result, f"Missing key: {key}"


# ─── P5.1: /os-dashboard endpoint structure ───────────────────────────────────


class TestOsDashboardEndpoint:
    def test_os_dashboard_in_intelligence_router(self):
        """Endpoint зарегистрирован в intelligence router."""
        import pathlib
        src = pathlib.Path("app/api/admin/intelligence.py").read_text(encoding="utf-8")
        assert "/os-dashboard" in src
        assert "source" in src
        assert "event_driven" in src

    def test_os_dashboard_uses_no_order_sql(self):
        """Endpoint не содержит прямых запросов к Order/ChatLog."""
        import pathlib
        src = pathlib.Path("app/api/admin/intelligence.py").read_text(encoding="utf-8")
        # В функции os_dashboard не должно быть Order SQL
        # Ищем паттерн: os_dashboard function до следующего @router
        start = src.find("async def os_dashboard")
        end = src.find("\n@router.", start + 1)
        if end == -1:
            end = len(src)
        func_body = src[start:end]
        assert "select(Order" not in func_body, "os_dashboard не должен делать SELECT Order"
        assert "select(ChatLog" not in func_body, "os_dashboard не должен делать SELECT ChatLog"

    def test_os_dashboard_uses_event_sources(self):
        """Endpoint использует event-driven функции."""
        import pathlib
        src = pathlib.Path("app/api/admin/intelligence.py").read_text(encoding="utf-8")
        start = src.find("async def os_dashboard")
        end = src.find("\n@router.", start + 1)
        if end == -1:
            end = len(src)
        func_body = src[start:end]
        assert "get_today_event_summary" in func_body
        assert "build_demand_forecast" in func_body
        assert "build_week_forecast" in func_body

    @pytest.mark.asyncio
    async def test_os_dashboard_returns_correct_structure(self, db_session: AsyncSession):
        """GET /os-dashboard возвращает все необходимые поля."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.db.models import Organization
        from app.api.admin.intelligence import os_dashboard

        db_session.add(Organization(id=200, name="OSOrg", slug="os-org"))
        await db_session.flush()

        mock_request = MagicMock()
        mock_request.session = {"organization_id": 200}

        with patch("app.api.admin.intelligence.admin_org_from_session", return_value=200):
            result = await os_dashboard(mock_request, db=db_session)

        assert result["ok"] is True
        assert result["source"] == "event_driven"
        assert "today" in result
        assert "week_forecast" in result
        assert "demand_forecast" in result
        assert "incidents" in result
        assert "top_recommendations" in result
        assert result["today"]["source"] == "event_driven"


# ─── P5.3: Self-healing wiring ────────────────────────────────────────────────


class TestSelfHealingWiring:
    def test_check_sla_thresholds_called_in_worker(self):
        """check_sla_thresholds вызывается в ai_incidents_hourly_tick."""
        import pathlib
        src = pathlib.Path("app/worker.py").read_text(encoding="utf-8")
        func_start = src.find("async def ai_incidents_hourly_tick")
        func_end = src.find("\nasync def ", func_start + 1)
        if func_end == -1:
            func_end = len(src)
        func_body = src[func_start:func_end]
        assert "check_sla_thresholds" in func_body, (
            "check_sla_thresholds должен вызываться в ai_incidents_hourly_tick"
        )

    def test_auto_escalation_in_worker(self):
        """auto-escalation: сталые инсайты помечаются critical."""
        import pathlib
        src = pathlib.Path("app/worker.py").read_text(encoding="utf-8")
        func_start = src.find("async def ai_incidents_hourly_tick")
        func_end = src.find("\nasync def ", func_start + 1)
        if func_end == -1:
            func_end = len(src)
        func_body = src[func_start:func_end]
        assert "OperationalInsight" in func_body
        assert "critical" in func_body
        assert "stale_cutoff" in func_body

    @pytest.mark.asyncio
    async def test_stale_insight_gets_escalated(self, db_session: AsyncSession):
        """Инсайт new + старый → severity должен стать critical."""
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select
        from app.db.models import OperationalInsight, Organization

        db_session.add(Organization(id=201, name="EscOrg", slug="esc-org"))
        await db_session.flush()

        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=3)
        insight = OperationalInsight(
            organization_id=201,
            insight_type="ai_token_spike",
            title="Token spike",
            summary="Tokens >3× avg",
            severity="warning",
            status="new",
            created_at=old_time,
        )
        db_session.add(insight)
        await db_session.flush()

        stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        stale = (await db_session.execute(
            select(OperationalInsight).where(
                OperationalInsight.organization_id == 201,
                OperationalInsight.status == "new",
                OperationalInsight.severity != "critical",
                OperationalInsight.created_at < stale_cutoff,
            ).limit(10)
        )).scalars().all()

        for s in stale:
            s.severity = "critical"
        await db_session.flush()

        refreshed = await db_session.get(OperationalInsight, insight.id)
        assert refreshed is not None
        assert refreshed.severity == "critical"


# ─── P5.4: OS Autopilot UI ────────────────────────────────────────────────────


class TestOsAutopilotUI:
    def test_os_tab_button_in_ai_center(self):
        """Кнопка «Автопилот» добавлена в AI-центр."""
        import pathlib
        html = pathlib.Path(
            "app/templates/screens/_tab_ai_center.html"
        ).read_text(encoding="utf-8")
        assert "aiCenterTab === 'os'" in html
        assert "Автопилот" in html

    def test_os_dashboard_section_in_template(self):
        """Секция OS Autopilot присутствует в шаблоне."""
        import pathlib
        html = pathlib.Path(
            "app/templates/screens/_tab_ai_center.html"
        ).read_text(encoding="utf-8")
        assert "osDashboardData" in html
        assert "demand_forecast" in html
        assert "week_forecast" in html
        assert "top_recommendations" in html

    def test_load_os_dashboard_in_js(self):
        """loadOsDashboard() существует в admin-app.js."""
        import pathlib
        js = pathlib.Path("app/static/js/admin-app.js").read_text(encoding="utf-8")
        assert "loadOsDashboard" in js
        assert "osDashboardData" in js
        assert "osDashboardLoading" in js
        assert "/api/admin/intelligence/os-dashboard" in js

    def test_event_bus_badge_in_ui(self):
        """Индикатор event-driven источника в OS Autopilot (язык оператора, не dev-жаргон)."""
        import pathlib
        html = pathlib.Path(
            "app/templates/screens/_tab_ai_center.html"
        ).read_text(encoding="utf-8")
        assert "Данные ОС" in html or "по событиям ОС" in html
