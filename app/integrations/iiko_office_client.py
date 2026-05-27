"""
Асинхронный клиент iiko Office (склад / остатки).

Конфигурация в ``Organization.integration_config_json``:

.. code-block:: json

    {
      "iiko_office": {
        "host": "https://office.example.iiko.it",
        "login": "api_user",
        "password_enc": "<Fernet token>",
        "store_id": "<UUID склада>",
        "department_id": "<UUID подразделения, опционально>"
      }
    }

Секреты: ``password_enc`` через ``encrypt_secret`` (``APP_SECRETS_FERNET_KEY``),
как ``iiko_api_login_enc`` в ``org_iiko.py``.

Эндпоинты (типовой REST iiko Office; без live-кредов — тесты через fixture):

- ``POST {host}/resto/api/auth`` — логин/пароль → ключ сессии
- ``GET {host}/resto/api/v2/reports/balance/stores`` — остатки по складу
  (query: ``store`` = store_id, ``department`` = department_id при наличии)

Формат ответа (нормализуется в ``IikoOfficeStockRow``):

.. code-block:: json

    {"items": [{"productId": "...", "productNum": "SKU", "productName": "...",
                "unit": "кг", "amount": 1.0, "minAmount": 3.0}]}
"""

from __future__ import annotations

import json
import logging
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.services.org_iiko_office import OrgIikoOfficeCredentials

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30.0
AUTH_PATH = "/resto/api/auth"
BALANCE_PATH = "/resto/api/v2/reports/balance/stores"
WAITER_SALES_PATH = "/resto/api/v2/reports/sales/waiters"
OLAP_COLUMNS_PATH = "/resto/api/v2/reports/olap/columns"
OLAP_REPORT_PATH = "/resto/api/v2/reports/olap"


@dataclass(frozen=True)
class IikoOfficeWaiterSalesRow:
    waiter_id: str
    waiter_name: str
    orders_count: int
    total_revenue: float
    guests_count: int
    cancelled_orders: int
    avg_service_time_min: float | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class IikoOfficeStockRow:
    product_id: str
    sku: str
    name: str
    unit: str
    quantity: float
    min_quantity: float | None
    raw: dict[str, Any]


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_stock_balances_payload(data: dict[str, Any] | list[Any]) -> list[IikoOfficeStockRow]:
    """Разбор ответа iiko Office / fixture в единый список строк остатков."""
    items: list[Any]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        raw_items = data.get("items") or data.get("stockBalances") or data.get("balances")
        items = raw_items if isinstance(raw_items, list) else []
    else:
        items = []

    rows: list[IikoOfficeStockRow] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        product_id = str(
            entry.get("productId")
            or entry.get("product_id")
            or entry.get("id")
            or "",
        ).strip()
        sku = str(
            entry.get("productNum")
            or entry.get("num")
            or entry.get("sku")
            or product_id,
        ).strip()
        name = str(
            entry.get("productName")
            or entry.get("name")
            or entry.get("ingredient")
            or sku,
        ).strip()
        if not sku and not product_id:
            continue
        rows.append(
            IikoOfficeStockRow(
                product_id=product_id or sku,
                sku=sku or product_id,
                name=name,
                unit=str(entry.get("unit") or entry.get("measureUnit") or "").strip(),
                quantity=_coerce_float(entry.get("amount") or entry.get("quantity")),
                min_quantity=(
                    _coerce_float(entry.get("minAmount") or entry.get("min_quantity"))
                    if entry.get("minAmount") is not None or entry.get("min_quantity") is not None
                    else None
                ),
                raw=entry,
            ),
        )
    return rows


def parse_waiter_sales_payload(data: dict[str, Any] | list[Any]) -> list[IikoOfficeWaiterSalesRow]:
    """Разбор отчёта продаж по офiciантам iiko Office / fixture."""
    items: list[Any]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        raw_items = data.get("items") or data.get("waiters") or data.get("rows")
        items = raw_items if isinstance(raw_items, list) else []
    else:
        items = []

    rows: list[IikoOfficeWaiterSalesRow] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        waiter_id = str(
            entry.get("waiterId")
            or entry.get("employeeId")
            or entry.get("waiter_id")
            or entry.get("id")
            or "",
        ).strip()
        if not waiter_id:
            continue
        waiter_name = str(
            entry.get("waiterName")
            or entry.get("employeeName")
            or entry.get("name")
            or waiter_id,
        ).strip()
        orders_count = int(_coerce_float(entry.get("ordersCount") or entry.get("orders") or 0))
        total_revenue = _coerce_float(entry.get("sum") or entry.get("revenue") or entry.get("total"))
        guests_count = int(_coerce_float(entry.get("guests") or entry.get("guestCount") or 0))
        cancelled_orders = int(
            _coerce_float(entry.get("cancellations") or entry.get("cancelledOrders") or 0),
        )
        avg_raw = entry.get("avgServiceTimeMin") or entry.get("avg_service_time_min")
        avg_service_time_min = _coerce_float(avg_raw) if avg_raw is not None else None
        rows.append(
            IikoOfficeWaiterSalesRow(
                waiter_id=waiter_id,
                waiter_name=waiter_name,
                orders_count=orders_count,
                total_revenue=total_revenue,
                guests_count=guests_count,
                cancelled_orders=cancelled_orders,
                avg_service_time_min=avg_service_time_min,
                raw=entry,
            ),
        )
    return rows


def _first_present(entry: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in entry and entry.get(key) not in (None, ""):
            return entry.get(key)
    return None


def parse_waiter_sales_olap_payload(data: dict[str, Any]) -> list[IikoOfficeWaiterSalesRow]:
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    rows: list[IikoOfficeWaiterSalesRow] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        waiter_name = str(
            _first_present(
                entry,
                (
                    "Waiter.Name",
                    "WaiterName",
                    "Waiter",
                    "Employee.Name",
                    "EmployeeName",
                    "User.Name",
                    "UserName",
                ),
            )
            or "",
        ).strip()
        waiter_id = str(
            _first_present(
                entry,
                (
                    "Waiter.Id",
                    "WaiterName.ID",
                    "OrderWaiter.Id",
                    "Waiter",
                    "Employee.Id",
                    "User.Id",
                    "WaiterName",
                    "Waiter.Name",
                ),
            )
            or waiter_name,
        ).strip()
        if not waiter_id and not waiter_name:
            continue
        if not waiter_name:
            waiter_name = waiter_id
        orders_count = int(
            _coerce_float(_first_present(entry, ("UniqOrderId", "OrderNum", "OrderCount", "OrdersCount"))),
        )
        total_revenue = _coerce_float(
            _first_present(entry, ("DishSumInt", "DishDiscountSumInt", "fullSum", "FullSum", "Revenue")),
        )
        guests_count = int(_coerce_float(_first_present(entry, ("GuestNum", "GuestsCount", "GuestCount"))))
        cancelled_orders = int(_coerce_float(_first_present(entry, ("DeletedOrders", "CancelledOrders"))))
        rows.append(
            IikoOfficeWaiterSalesRow(
                waiter_id=waiter_id,
                waiter_name=waiter_name,
                orders_count=orders_count,
                total_revenue=total_revenue,
                guests_count=guests_count,
                cancelled_orders=cancelled_orders,
                avg_service_time_min=None,
                raw=entry,
            ),
        )
    return rows


def _pick_olap_field(
    columns: dict[str, Any],
    candidates: tuple[str, ...],
    *,
    capability: str,
) -> str | None:
    for key in candidates:
        meta = columns.get(key)
        if isinstance(meta, dict) and meta.get(capability) is True:
            return key
    for key, meta in columns.items():
        if not isinstance(meta, dict) or meta.get(capability) is not True:
            continue
        name = str(meta.get("name") or key).lower()
        key_l = str(key).lower()
        if any(token in key_l or token in name for token in ("waiter", "официант")):
            return str(key)
    return None


def _normalize_olap_datetime(value: str, *, end: bool = False) -> str:
    raw = str(value or "").strip()
    date_part = raw.split("T", 1)[0]
    if not date_part:
        return raw
    if not end:
        return date_part
    try:
        return (datetime.strptime(date_part, "%Y-%m-%d") + timedelta(days=1)).date().isoformat()
    except ValueError:
        return date_part


def _session_key_from_auth_response(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type:
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            return str(data.get("key") or data.get("token") or data.get("sessionKey") or "").strip()
        if isinstance(data, str):
            return data.strip().strip('"')

    text = (response.text or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return str(data.get("key") or data.get("token") or data.get("sessionKey") or "").strip()
    return text.strip('"')


def _office_password_candidates(password: str) -> list[str]:
    value = str(password or "").strip()
    if not value:
        return [""]
    lower = value.lower()
    is_sha1_hex = len(lower) == 40 and all(ch in "0123456789abcdef" for ch in lower)
    candidates = [lower] if is_sha1_hex else [hashlib.sha1(value.encode("utf-8")).hexdigest(), value]
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


class IikoOfficeClient:
    """httpx-клиент iiko Office с опциональной подгрузкой fixture (тесты / dev)."""

    def __init__(
        self,
        creds: OrgIikoOfficeCredentials,
        *,
        fixture_path: str | Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        authenticate_on_enter: bool = True,
    ) -> None:
        self._creds = creds
        self._fixture_path = Path(fixture_path) if fixture_path else None
        self._transport = transport
        self._authenticate_on_enter = authenticate_on_enter
        self._session_key: str | None = None
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "IikoOfficeClient":
        self._http = httpx.AsyncClient(
            base_url=self._creds.host.rstrip("/"),
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            transport=self._transport,
        )
        if self._fixture_path is None and self._authenticate_on_enter:
            await self._authenticate()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    async def _authenticate(self) -> None:
        if not self._http:
            raise RuntimeError("HTTP client not initialized")
        response: httpx.Response | None = None
        attempts: list[tuple[str, dict[str, Any]]] = []
        for password in _office_password_candidates(self._creds.password):
            attempts.extend(
                [
                    ("POST", {"params": {"login": self._creds.login, "pass": password}}),
                    ("POST", {"data": {"login": self._creds.login, "pass": password}}),
                    ("POST", {"json": {"login": self._creds.login, "pass": password}}),
                    ("GET", {"params": {"login": self._creds.login, "pass": password}}),
                ],
            )
        for method, kwargs in attempts:
            response = await self._http.request(method, AUTH_PATH, **kwargs)
            if response.status_code < 400:
                break
            if response.status_code not in {400, 401, 403, 404, 405, 415, 500}:
                response.raise_for_status()
        if response is None:
            raise RuntimeError("iiko Office auth: request was not sent")
        response.raise_for_status()
        key = _session_key_from_auth_response(response)
        if not key:
            raise ValueError("iiko Office auth: ответ без key/token")
        self._session_key = str(key)
        logger.info("iiko Office: авторизация успешна host=%s", self._creds.host)

    def _auth_params(self) -> dict[str, str]:
        if not self._session_key:
            raise RuntimeError("iiko Office: нет ключа сессии")
        return {"key": self._session_key}

    async def fetch_stock_balances(self, *, timestamp: str | None = None) -> list[IikoOfficeStockRow]:
        """
        Остатки по складу из iiko Office.
        При ``fixture_path`` читает JSON с диска (без HTTP).
        """
        if self._fixture_path is not None:
            raw = json.loads(self._fixture_path.read_text(encoding="utf-8"))
            return parse_stock_balances_payload(raw)

        if not self._http:
            raise RuntimeError("HTTP client not initialized")
        params: dict[str, str] = {
            **self._auth_params(),
            "store": self._creds.store_id,
            "timestamp": timestamp or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if self._creds.department_id:
            params["department"] = self._creds.department_id
        response = await self._http.get(BALANCE_PATH, params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("iiko Office balance: ожидался JSON object")
        return parse_stock_balances_payload(data)

    async def fetch_olap_columns(self, *, report_type: str = "SALES") -> dict[str, Any]:
        if not self._http:
            raise RuntimeError("HTTP client not initialized")
        response = await self._http.get(
            OLAP_COLUMNS_PATH,
            params={**self._auth_params(), "reportType": report_type},
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def fetch_waiter_sales_report_olap(
        self,
        *,
        date_from: str,
        date_to: str,
    ) -> list[IikoOfficeWaiterSalesRow]:
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        columns = await self.fetch_olap_columns(report_type="SALES")
        waiter_name_field = _pick_olap_field(
            columns,
            ("WaiterName", "OrderWaiter.Name", "Waiter.Name", "Employee.Name", "User.Name"),
            capability="groupingAllowed",
        ) or "Waiter.Name"
        waiter_id_field = _pick_olap_field(
            columns,
            ("WaiterName.ID", "OrderWaiter.Id", "Waiter.Id", "Employee.Id", "User.Id"),
            capability="groupingAllowed",
        )

        group_fields = [waiter_name_field]
        if waiter_id_field and waiter_id_field != waiter_name_field:
            group_fields.insert(0, waiter_id_field)

        aggregate_fields: list[str] = []
        for key in ("DishSumInt", "DishDiscountSumInt", "UniqOrderId", "OrderNum", "GuestNum"):
            meta = columns.get(key)
            if not columns or (isinstance(meta, dict) and meta.get("aggregationAllowed") is True):
                aggregate_fields.append(key)
        if not aggregate_fields:
            aggregate_fields = ["DishSumInt", "UniqOrderId"]

        filters: dict[str, Any] = {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": _normalize_olap_datetime(date_from),
                "to": _normalize_olap_datetime(date_to, end=True),
            },
            "DeletedWithWriteoff": {
                "filterType": "IncludeValues",
                "values": ["NOT_DELETED"],
            },
            "OrderDeleted": {
                "filterType": "IncludeValues",
                "values": ["NOT_DELETED"],
            },
        }
        if self._creds.department_id:
            filters["Department.Id"] = {
                "filterType": "IncludeValues",
                "values": [self._creds.department_id],
            }

        payload = {
            "reportType": "SALES",
            "buildSummary": False,
            "groupByRowFields": group_fields,
            "aggregateFields": aggregate_fields,
            "filters": filters,
        }
        response = await self._http.post(
            OLAP_REPORT_PATH,
            params=self._auth_params(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("iiko Office OLAP waiter sales: expected JSON object")
        return parse_waiter_sales_olap_payload(data)

    async def fetch_waiter_sales_report(
        self,
        *,
        date_from: str,
        date_to: str,
        waiter_fixture_path: str | Path | None = None,
    ) -> list[IikoOfficeWaiterSalesRow]:
        """
        Отчёт продаж по офiciантам за период (обычно один день).
        ``waiter_fixture_path`` — отдельный fixture для KPI (тесты).
        """
        if waiter_fixture_path is not None:
            raw = json.loads(Path(waiter_fixture_path).read_text(encoding="utf-8"))
            return parse_waiter_sales_payload(raw)

        if not self._http:
            raise RuntimeError("HTTP client not initialized")
        params: dict[str, str] = {
            **self._auth_params(),
            "dateFrom": date_from,
            "dateTo": date_to,
        }
        if self._creds.department_id:
            params["department"] = self._creds.department_id
        response = await self._http.get(WAITER_SALES_PATH, params=params)
        if response.status_code in {404, 405}:
            return await self.fetch_waiter_sales_report_olap(
                date_from=date_from,
                date_to=date_to,
            )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return parse_waiter_sales_payload(data)
        if isinstance(data, list):
            return parse_waiter_sales_payload(data)
        raise ValueError("iiko Office waiter sales: ожидался JSON object или array")
