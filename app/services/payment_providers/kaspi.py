"""
Kaspi Pay webhook adapter.

Kaspi отправляет POST с JSON-телом. Подпись: HMAC-SHA256(raw_body, secret) в hex,
передаётся в заголовке X-Kaspi-Signature (или X-Hub-Signature-256 у некоторых версий).

Для включения: задайте KASPI_HMAC_SECRET в .env.

Поля webhook (Kaspi Business API / Kaspi Pay):
  txn_id        — ID транзакции Kaspi
  order_id      — наш order_id (передаём при создании платежа)
  amount        — сумма в тенге
  status        — "SUCCESS" / "FAILED" / "REVERSED"
  merchant_id   — идентификатор мерчанта

organization_id передаётся в дополнительном поле org_id или извлекается из order_id.
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

# Заголовки, в которых Kaspi может передавать подпись
_SIG_HEADERS = ("X-Kaspi-Signature", "X-Hub-Signature-256", "X-Signature")


class KaspiWebhookAdapter:
    provider_slug: ClassVar[str] = "kaspi"

    async def verify(self, request: Request, raw_body: bytes) -> bool:
        secret = (settings.kaspi_webhook_hmac_secret or "").strip()
        if not secret:
            return False

        expected = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        for header_name in _SIG_HEADERS:
            got = (request.headers.get(header_name) or "").strip()
            # Kaspi может передавать "sha256=<hex>" или просто "<hex>"
            if got.startswith("sha256="):
                got = got[7:]
            if got and secrets.compare_digest(expected, got.lower()):
                return True

        return False

    async def parse(self, raw_body: bytes) -> ParsedPayment:
        data: dict[str, Any] = json.loads(raw_body.decode("utf-8"))

        # Kaspi передаёт txn_id как ID транзакции
        payment_id = str(
            data.get("txn_id") or data.get("payment_id") or data.get("transaction_id") or ""
        ).strip()

        # order_id — наш внутренний ID заказа
        raw_oid = data.get("order_id") or data.get("orderId") or "0"
        order_id = int(str(raw_oid).split(":")[0])

        # organization_id — может быть в отдельном поле или закодирован в order_id
        raw_org = data.get("org_id") or data.get("organization_id") or data.get("merchant_id") or "0"
        try:
            organization_id = int(str(raw_org))
        except (ValueError, TypeError):
            organization_id = 0

        # Kaspi: SUCCESS → paid, остальное → failed
        raw_status = str(data.get("status") or data.get("result") or "").strip().upper()
        status = "paid" if raw_status in ("SUCCESS", "APPROVED", "PAID", "1") else "failed"

        amt_raw = data.get("amount") or data.get("sum")
        amount_f = float(amt_raw) if amt_raw is not None else None

        return ParsedPayment(
            order_id=order_id,
            organization_id=organization_id,
            payment_id=payment_id or f"kaspi_{raw_oid}",
            status=status,
            amount=amount_f,
            raw=data,
        )
