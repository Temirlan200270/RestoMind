from __future__ import annotations

import pytest

from app.integrations.iiko_server_client import password_for_server_auth
from app.services.copilot.engine import _period_from_question, _select_tools
from app.services.iiko_olap_sales_sync import _parse_date, _parse_decimal, _row_get


def test_iiko_server_password_hashing_idempotent() -> None:
    hashed = password_for_server_auth("secret")
    assert len(hashed) == 40
    assert password_for_server_auth(hashed.upper()) == hashed


def test_olap_row_helpers_accept_iiko_aliases() -> None:
    row = {"UniqOrderId.Id": "ord-1", "OpenDate.Typed": "2026-06-03", "DishDiscountSumInt": "1250.50"}
    assert _row_get(row, "OrderId", "UniqOrderId.Id") == "ord-1"
    assert _parse_date(row["OpenDate.Typed"]).isoformat() == "2026-06-03"
    assert float(_parse_decimal(row["DishDiscountSumInt"])) == pytest.approx(1250.50)


def test_copilot_selects_safe_tools() -> None:
    names = _select_tools("Почему вчера упала выручка по категориям?")
    assert "get_revenue_summary" in names
    assert "get_anomalies" in names
    assert "get_category_breakdown" in names
    assert _period_from_question("Сколько заработали вчера?") == "yesterday"
