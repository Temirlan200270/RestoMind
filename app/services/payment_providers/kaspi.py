"""
Kaspi Pay: HMAC-SHA256 верификация подписи вебхука.

Kaspi передаёт подпись в заголовке X-Kaspi-Signature (hex HMAC-SHA256 тела).
Без секрета или подписи — возвращаем False.

NOTE: уточните точное название заголовка и алгоритм хеширования по документации
Kaspi Pay для вашего типа интеграции (e-commerce / QR).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any, ClassVar

from starlette.requests import Request

from app.core.config import settings
from app.services.payment_adapters import ParsedPayment

_KASPI_SIG_HEADERS = ("x-kaspi-signature", "x-kaspi-hmac", "x-signature-256")


class KaspiWebhookAdapter:
    provider_slug: ClassVar[str] = "kaspi"

    async def verify(self, request: Request, raw_body: bytes) -> bool:
        secret = (settings.kaspi_webhook_hmac_secret or "").strip()
        if not secret:
            return False
        key = secret.encode()
        expected = hmac.new(key, raw_body, hashlib.sha256).hexdigest()
        for header_name in _KASPI_SIG_HEADERS:
            got = (request.headers.get(header_name) or "").strip().lower()
            if got and secrets.compare_digest(got, expected):
                return True
        return False

    async def parse(self, raw_body: bytes) -> ParsedPayment:
        data: dict[str, Any] = json.loads(raw_body.decode("utf-8"))
        oid = int(data["order_id"])
        gid = int(data["organization_id"])
        pid = str(data["payment_id"]).strip()
        status = "failed" if str(data.get("status") or "").strip().lower() == "failed" else "paid"
        amt = data.get("amount")
        amount_f = float(amt) if amt is not None else None
        return ParsedPayment(
            order_id=oid,
            organization_id=gid,
            payment_id=pid,
            status=status,
            amount=amount_f,
            raw=data,
        )
