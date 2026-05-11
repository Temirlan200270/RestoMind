"""
CloudPayments: проверка Content-HMAC (base64(HMAC-SHA256(body))) и разбор уведомления.
В поле Data ожидается JSON: {\"order_id\", \"organization_id\"} (опционально payment_id).
Иначе payment_id = TransactionId.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

import httpx
from starlette.requests import Request

from app.core.config import settings
from app.services.payment_adapters import ParsedPayment
from app.services.payment_providers.base import InitiatedPayment

_CLOUDPAYMENTS_API = "https://api.cloudpayments.ru"
_PAYMENT_TTL_MINUTES = 60


class CloudPaymentsWebhookAdapter:
    provider_slug: ClassVar[str] = "cloudpayments"

    async def verify(self, request: Request, raw_body: bytes) -> bool:
        secret = (settings.cloudpayments_api_secret or "").strip().encode()
        if not secret:
            return False
        expected = base64.b64encode(
            hmac.new(secret, raw_body, hashlib.sha256).digest(),
        ).decode("ascii")
        got = (request.headers.get("Content-HMAC") or "").strip()
        if not got:
            return False
        return secrets.compare_digest(expected.strip(), got.strip())

    async def parse(self, raw_body: bytes) -> ParsedPayment:
        data: dict[str, Any] = json.loads(raw_body.decode("utf-8"))
        tx = data.get("TransactionId")
        payment_id = str(tx).strip() if tx is not None else ""
        if not payment_id:
            payment_id = str(data.get("InvoiceId") or "unknown")

        amount_raw = data.get("Amount")
        amount_f = float(amount_raw) if amount_raw is not None else None

        status_raw = str(data.get("Status") or "").strip()
        st_lower = status_raw.lower()
        status = "paid" if st_lower in ("completed", "authorized", "auth") else "failed"

        inner: dict[str, Any] = {}
        dr = data.get("Data")
        if isinstance(dr, str) and dr.strip():
            try:
                parsed = json.loads(dr)
                if isinstance(parsed, dict):
                    inner = parsed
            except json.JSONDecodeError:
                inner = {}

        order_id = int(inner.get("order_id") or data.get("InvoiceId") or 0)
        organization_id = int(inner.get("organization_id") or 0)
        if order_id <= 0 or organization_id <= 0:
            raise ValueError("cloudpayments_missing_order_or_org")

        return ParsedPayment(
            order_id=order_id,
            organization_id=organization_id,
            payment_id=payment_id[:200],
            status="paid" if status == "paid" else "failed",
            amount=amount_f,
            raw=data,
        )


class CloudPaymentsInitiator:
    """Создаёт платёжную сессию через CloudPayments API (hosted payment page)."""

    provider_slug: ClassVar[str] = "cloudpayments"

    async def create_payment(
        self,
        *,
        order_id: int,
        amount: float,
        currency: str,
        description: str,
        idempotency_key: str,
        callback_url: str,
        success_url: str,
        credentials: dict[str, str],
    ) -> InitiatedPayment:
        public_id = credentials.get("api_key", "")
        api_secret = credentials.get("secret_key", "")

        if not public_id or not api_secret:
            raise ValueError("CloudPayments: api_key (PublicId) и secret_key обязательны")

        payload: dict[str, Any] = {
            "Amount": round(amount, 2),
            "Currency": currency,
            "Description": description[:255],
            "AccountId": str(order_id),
            "InvoiceId": idempotency_key[:200],
        }
        if callback_url:
            payload["CallbackUrl"] = callback_url
        if success_url:
            payload["SuccessRedirectUrl"] = success_url

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_CLOUDPAYMENTS_API}/payments/link/create",
                auth=(public_id, api_secret),
                json=payload,
            )
            resp.raise_for_status()

        data = resp.json()
        if not data.get("Success"):
            msg = data.get("Message") or "CloudPayments: неизвестная ошибка"
            raise ValueError(f"CloudPayments initiation error: {msg}")

        model = data.get("Model") or {}
        payment_url = str(model.get("Url") or "")
        payment_id = str(model.get("Id") or "")

        if not payment_url:
            raise ValueError(f"CloudPayments не вернул URL: {data!r}")

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_PAYMENT_TTL_MINUTES)

        return InitiatedPayment(
            payment_url=payment_url,
            provider_payment_id=payment_id,
            expires_at=expires_at,
            raw=model,
        )
