from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import MenuItem, Organization
from app.integrations.iiko_server_client import IikoServerClient, normalize_server_host
from app.services.iiko_food_cost_sync import sync_food_cost_for_org


@pytest.mark.asyncio
async def test_iiko_server_fetch_product_expenses_uses_stock_olap(monkeypatch) -> None:
    client = IikoServerClient(
        host="example.iiko.it",
        login="readonly",
        password="secret",
        department_id="dep-1",
    )
    seen: dict[str, Any] = {}

    async def fake_request(method: str, path: str, *, json: dict, timeout: float) -> dict:
        seen.update({"method": method, "path": path, "json": json, "timeout": timeout})
        return {"data": [{"DishId": "p1", "ProductCostBase.ProductCost": 123}]}

    monkeypatch.setattr(client, "_request", fake_request)
    rows = await client.fetch_product_expenses("cloud-org", date(2026, 6, 1), date(2026, 6, 3))

    assert rows == [{"DishId": "p1", "ProductCostBase.ProductCost": 123}]
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v2/reports/olap"
    assert seen["json"]["reportType"] == "STOCK"
    assert seen["json"]["filters"]["Department.Id"]["values"] == ["dep-1"]


def test_iiko_server_host_normalization() -> None:
    assert normalize_server_host("saida-co.iiko.it", 443) == "https://saida-co.iiko.it/resto"
    assert normalize_server_host("https://saida-co.iiko.it/resto", 443) == "https://saida-co.iiko.it/resto"
    assert normalize_server_host("http://localhost", 8080) == "http://localhost:8080/resto"


@pytest.mark.asyncio
async def test_food_cost_sync_uses_sales_client_factory(monkeypatch, db_session) -> None:
    db_session.add(Organization(id=1, name="Cost Org", slug="cost-org"))
    db_session.add(
        MenuItem(
            organization_id=1,
            iiko_id="dish-1",
            name="Plov",
            price=1000,
            is_available=True,
            is_archived=False,
        ),
    )
    await db_session.flush()

    @dataclass
    class Creds:
        api_login: str = "cloud-login"
        iiko_organization_id: str = "cloud-org"
        terminal_group_id: str = ""

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def fetch_product_expenses(self, organization_id: str, date_from: date, date_to: date) -> list[dict[str, Any]]:
            assert organization_id == "cloud-org"
            return [{"DishId": "dish-1", "DishName": "Plov", "ProductCostBase.ProductCost": 333.33}]

    async def fake_resolve(db, organization_id: int):
        return FakeClient(), Creds(), "server"

    monkeypatch.setattr("app.services.iiko_food_cost_sync.resolve_iiko_sales_client", fake_resolve)
    updated = await sync_food_cost_for_org(db_session, 1, date(2026, 6, 1), date(2026, 6, 3))

    item = await db_session.scalar(select(MenuItem).where(MenuItem.organization_id == 1))
    assert updated == 1
    assert float(item.cost_price) == pytest.approx(333.33)
