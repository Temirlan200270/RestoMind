"""Async iiko Server (Resto) client for OLAP v2 reports."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 90.0
MAX_RETRIES = 2
RETRY_DELAY = 1.0
_SHA1_HEX_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def password_for_server_auth(password: str) -> str:
    """Plain password -> SHA1(hex); already-hashed values pass through."""
    raw = (password or "").strip()
    if _SHA1_HEX_RE.fullmatch(raw):
        return raw.lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class IikoServerClient:
    """iiko Server /resto API client used only for read-only OLAP ingestion."""

    def __init__(
        self,
        *,
        host: str,
        login: str,
        password: str,
        port: int = 443,
        department_id: str = "",
    ) -> None:
        self._host = (host or "").strip()
        self._login = (login or "").strip()
        self._password = password or ""
        self._port = int(port or 443)
        self._department_id = (department_id or "").strip()
        self._token: str | None = None
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "IikoServerClient":
        if not self._host:
            raise ValueError("IIKO_SERVER_HOST не задан")
        if not self._login or not self._password:
            raise ValueError("IIKO_SERVER_LOGIN/IIKO_SERVER_PASSWORD не заданы")
        self._http = httpx.AsyncClient(
            base_url=f"https://{self._host}:{self._port}/resto",
            timeout=REQUEST_TIMEOUT,
        )
        await self._authenticate()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http and self._token:
            try:
                await self._http.get("/api/logout", params={"key": self._token})
            except httpx.HTTPError:
                logger.debug("iiko Server logout failed", exc_info=True)
        if self._http:
            await self._http.aclose()

    async def _authenticate(self) -> None:
        if not self._http:
            raise RuntimeError("HTTP-клиент не инициализирован. Используйте async with.")
        response = await self._http.get(
            "/api/auth",
            params={
                "login": self._login,
                "pass": password_for_server_auth(self._password),
            },
        )
        response.raise_for_status()
        token = (response.text or "").strip()
        if not token:
            raise ValueError("iiko Server не вернул token в ответе /api/auth")
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self._http or not self._token:
            raise RuntimeError("iiko Server клиент не авторизован")

        last_exc: Exception | None = None
        refreshed = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await self._http.request(
                    method,
                    path,
                    params={"key": self._token},
                    json=dict(json) if json is not None else None,
                    timeout=timeout,
                )
                if response.status_code in (401, 403) and not refreshed:
                    await self._authenticate()
                    refreshed = True
                    response = await self._http.request(
                        method,
                        path,
                        params={"key": self._token},
                        json=dict(json) if json is not None else None,
                        timeout=timeout,
                    )
                response.raise_for_status()
                if not response.content:
                    return {}
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("iiko Server вернул неожиданный JSON (ожидали object)")
                return data
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 0
                if isinstance(exc, httpx.HTTPStatusError) and status < 500:
                    raise
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY * attempt)
        raise last_exc or RuntimeError(f"iiko Server: не удалось выполнить {method} {path}")

    async def fetch_olap_sales(
        self,
        organization_id: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        """OLAP v2 SALES rows; organization_id is accepted for client protocol symmetry."""
        filters: dict[str, Any] = {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
                "includeLow": True,
                "includeHigh": True,
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
        if self._department_id:
            filters["Department.Id"] = {
                "filterType": "IncludeValues",
                "values": [self._department_id],
            }

        body: dict[str, Any] = {
            "reportType": "SALES",
            "buildSummary": False,
            "groupByRowFields": [
                "UniqOrderId.Id",
                "OpenDate.Typed",
                "CloseTime",
                "WaiterName",
                "OrderType",
                "OriginName",
                "DishId",
                "DishName",
                "DishCategory",
            ],
            "groupByColFields": [],
            "aggregateFields": [
                "DishDiscountSumInt",
                "DishAmountInt",
                "GuestNum",
            ],
            "filters": filters,
        }
        data = await self._request(
            "POST",
            "/api/v2/reports/olap",
            json=body,
            timeout=120.0,
        )
        return self._extract_olap_rows(data)

    @staticmethod
    def _extract_olap_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        rows = payload.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        return []
