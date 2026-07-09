from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import OperationalInsight, Order, OrderStatus, Organization, User
from app.api.admin.intelligence import _executive_hub_degraded_payload
from app.services.executive_hub import build_executive_hub_payload


@pytest.mark.asyncio
async def test_build_executive_hub_payload_returns_scoped_cards(db_session):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    duration = now - today_start
    if duration.total_seconds() <= 0:
        duration = timedelta(hours=1)
    prev_start = today_start - duration
    cur_mid = today_start + (duration / 2)
    prev_mid = prev_start + (duration / 2)

    db_session.add_all(
        [
            Organization(id=1, name="Org 1"),
            Organization(id=2, name="Org 2"),
            User(id=1, organization_id=1, phone="+77000000001"),
            User(id=2, organization_id=2, phone="+77000000002"),
            Order(
                organization_id=1,
                user_id=1,
                status=OrderStatus.COMPLETED.value,
                total_price=12000,
                items_json={"items": [{"name": "Plov", "quantity": 1, "item_total": 12000}]},
                created_at=cur_mid,
            ),
            Order(
                organization_id=2,
                user_id=2,
                status=OrderStatus.COMPLETED.value,
                total_price=999999,
                items_json={"items": [{"name": "Other", "quantity": 1, "item_total": 999999}]},
                created_at=cur_mid,
            ),
            Order(
                organization_id=1,
                user_id=1,
                status=OrderStatus.COMPLETED.value,
                total_price=8000,
                items_json={"items": [{"name": "Lagman", "quantity": 1, "item_total": 8000}]},
                created_at=prev_mid,
            ),
            OperationalInsight(
                organization_id=1,
                insight_type="revenue_drop",
                severity="warning",
                title="Выручка ниже обычного",
                summary="Сегодня меньше заказов, чем в прошлом окне",
                status="new",
                payload_json={"cause_hypotheses": ["orders_drop"], "recommended_actions": ["Проверить стоп-лист"]},
            ),
        ]
    )
    await db_session.flush()

    payload = await build_executive_hub_payload(db_session, 1, role="owner")

    assert len(payload["cards"]) >= 3
    assert payload["cards"][0]["id"] == "revenue_pulse"
    assert payload["cards"][0]["metrics"]["revenue_kzt"] == 12000
    assert any(card["id"].startswith("insight_") for card in payload["cards"])
    assert payload["version"] == 4
    assert payload["summary"]["stats"]
    assert payload["summary"]["has_orders"] is True
    assert payload["today_picture"]["has_orders"] is True
    assert payload["today_picture"]["forecast"]
    assert len(payload["owner_cards"]) == 3
    assert {card["id"] for card in payload["owner_cards"]} == {"owner_money", "owner_ops", "owner_ai_clients"}
    assert payload["money_drivers"]
    assert payload["money_at_risk"]["rows"]
    assert payload["network_branch"] == {"enabled": False, "rows": [], "actions": []}
    assert payload["priority_signals"]
    assert payload["agent_context"]["title"] == "Разбор выбранного сигнала"
    assert payload["owner_readiness"]["mode"] in {"runtime", "onboarding"}
    assert payload["owner_readiness"]["onboarding"]
    assert all("focused_view" in card for card in payload["cards"])
    assert next(card for card in payload["cards"] if card["id"] == "revenue_pulse")["focused_view"]["kpis"]
    assert payload["next_actions"]
    assert payload["readiness"]["mode"] == "runtime"
    assert payload["dimensions"]["money"]["card_ids"]
    assert payload["cards"][0]["dimension"] == "money"
    assert any(item.get("action_type") == "navigate" for item in payload["cards"][0].get("action_items", []))
    assert payload["chat"]["endpoint"] == "/api/admin/intelligence/query"
    assert payload["chat"]["agent_actions_endpoint"] == "/api/admin/intelligence/agent-actions"
    assert payload["chat"]["business_questions"]


def test_executive_hub_degraded_payload_keeps_surface_loadable():
    payload = _executive_hub_degraded_payload(period="today", role="owner")

    assert payload["version"] == 4
    assert payload["period"] == "today"
    assert payload["cards"]
    assert payload["cards"][0]["id"] == "hub_degraded"
    assert payload["readiness"]["mode"] == "degraded"
    assert payload["chat"]["endpoint"] == "/api/admin/intelligence/query"
    assert payload["chat"]["business_questions"]


@pytest.mark.asyncio
async def test_build_executive_hub_payload_keeps_insights_after_later_rollback(db_session, monkeypatch):
    from app.services import executive_hub

    db_session.add_all(
        [
            Organization(id=1, name="Org 1"),
            OperationalInsight(
                organization_id=1,
                insight_type="revenue_drop",
                severity="warning",
                title="Revenue lower than usual",
                summary="Orders are lower than the comparable window",
                status="new",
                payload_json={
                    "cause_hypotheses": ["orders_drop"],
                    "recommended_actions": ["Check top menu items"],
                },
            ),
        ]
    )
    await db_session.flush()

    async def fail_owner_summary(*args, **kwargs):
        raise RuntimeError("owner summary failed after insights loaded")

    monkeypatch.setattr(executive_hub, "build_owner_intelligence_summary", fail_owner_summary)

    payload = await build_executive_hub_payload(db_session, 1, role="owner")

    insight_card = next(card for card in payload["cards"] if card["id"].startswith("insight_"))
    assert insight_card["headline"] == "Revenue lower than usual"
    assert insight_card["why"] == ["orders_drop"]


@pytest.mark.asyncio
async def test_build_executive_hub_payload_is_org_scoped(db_session):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Organization(id=1, name="Org 1"),
            Organization(id=2, name="Org 2"),
            User(id=1, organization_id=1, phone="+77000000001"),
            User(id=2, organization_id=2, phone="+77000000002"),
            Order(
                organization_id=2,
                user_id=2,
                status=OrderStatus.COMPLETED.value,
                total_price=500000,
                items_json={"items": [{"name": "Steak", "quantity": 1, "item_total": 500000}]},
                created_at=now,
            ),
        ]
    )
    await db_session.flush()

    payload = await build_executive_hub_payload(db_session, 1, role="owner")
    revenue_card = next(card for card in payload["cards"] if card["id"] == "revenue_pulse")

    assert revenue_card["metrics"]["revenue_kzt"] == 0
    assert payload["summary"]["has_orders"] is False
    assert payload["today_picture"]["has_orders"] is False
    assert payload["today_picture"]["forecast"]["available"] is False
    assert payload["today_picture"]["headline"] == "Сегодня ещё нет заказов"
    assert any(row["id"] == "create_test_order" for row in payload["next_actions"])
