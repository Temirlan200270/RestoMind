"""
Freedom Pay (freedompay.money) — webhook adapter + payment initiator.

Webhook: Freedom Pay присылает POST с form-encoded или JSON телом.
  Подпись: pg_sig = md5(script_name ';' sorted_params... ';' secret_key)
  Параметры сортируются по ключу (алфавитно), pg_sig исключается из расчёта.

Initiation: POST /v2/payment/merchant/checkout
  Документация: https://docs.freedompay.money
  Возвращает hosted payment page URL.

Тестовая среда: https://api.freedompay.money (тот же хост, параметр pg_testing_mode=1)
"""

from __future__ import annotations

import hashlib
import json
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

import httpx
from starlette.requests import Request

from app.core.config import settings
from app.services.payment_adapters import ParsedPayment
from app.services.payment_providers.base import InitiatedPayment

_FREEDOM_PAY_API = "https://api.freedompay.money"
_PAYMENT_TTL_MINUTES = 60


class FreedomPayWebhookAdapter:
    """Верификация и разбор webhook от Freedom Pay."""

    provider_slug: ClassVar[str] = "freedom_pay"

    async def verify(self, request: Request, raw_body: bytes) -> bool:
        secret = (settings.freedom_pay_webhook_secret or "").strip()
        if not secret:
            return False

        # Freedom Pay отправляет form-encoded или JSON
        try:
            params = dict(urllib.parse.parse_qsl(raw_body.decode("utf-8")))
        except Exception:
            try:
                params = json.loads(raw_body.decode("utf-8"))
            except Exception:
                return False

        received_sig = str(params.get("pg_sig") or "").strip()
        if not received_sig:
            # Fallback: HMAC-SHA256 заголовок X-Freedom-Signature
            header_sig = (request.headers.get("X-Freedom-Signature") or "").strip()
            if not header_sig:
                return False
            expected = hashlib.sha256(
                (secret + raw_body.decode("utf-8", errors="replace")).encode()
            ).hexdigest()
            return secrets.compare_digest(expected, header_sig)

        # Стандартная подпись Freedom Pay: md5(script ';' sorted_params ';' secret)
        script_name = str(params.get("pg_script_name") or "payment")
        sig_params = sorted(
            (k, str(v)) for k, v in params.items() if k != "pg_sig"
        )
        raw_str = script_name + ";" + ";".join(v for _, v in sig_params) + ";" + secret
        expected_md5 = hashlib.md5(raw_str.encode("utf-8")).hexdigest()
        return secrets.compare_digest(expected_md5, received_sig)

    async def parse(self, raw_body: bytes) -> ParsedPayment:
        try:
            params: dict[str, Any] = dict(
                urllib.parse.parse_qsl(raw_body.decode("utf-8"))
            )
        except Exception:
            params = json.loads(raw_body.decode("utf-8"))

        # pg_order_id — наш order_id (передаём при initiation)
        raw_order = params.get("pg_order_id") or params.get("order_id") or "0"
        # org_id может быть в дополнительном поле или в pg_description
        raw_org = params.get("pg_merchant_id") or params.get("organization_id") or "0"

        oid = int(str(raw_order).split(":")[0])
        try:
            gid = int(str(raw_org))
        except (ValueError, TypeError):
            gid = 0

        pid = str(params.get("pg_payment_id") or params.get("payment_id") or "").strip()

        # pg_result: 1 = успешно, 0 = отказ
        pg_result = str(params.get("pg_result") or "0").strip()
        status: str = "paid" if pg_result == "1" else "failed"

        amt_raw = params.get("pg_amount") or params.get("amount")
        amount_f = float(amt_raw) if amt_raw is not None else None

        return ParsedPayment(
            order_id=oid,
            organization_id=gid,
            payment_id=pid or f"fp_{raw_order}",
            status=status,
            amount=amount_f,
            raw=params,
        )


class FreedomPayInitiator:
    """Создаёт платёжную сессию через Freedom Pay API."""

    provider_slug: ClassVar[str] = "freedom_pay"

    def _build_signature(
        self,
        script_name: str,
        params: dict[str, str],
        secret_key: str,
    ) -> str:
        sig_params = sorted((k, str(v)) for k, v in params.items())
        raw_str = script_name + ";" + ";".join(v for _, v in sig_params) + ";" + secret_key
        return hashlib.md5(raw_str.encode("utf-8")).hexdigest()

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
        merchant_id = credentials.get("merchant_id", "")
        secret_key = credentials.get("secret_key", "")
        environment = credentials.get("environment", "production")

        if not merchant_id or not secret_key:
            raise ValueError("Freedom Pay: merchant_id и secret_key обязательны")

        params: dict[str, str] = {
            "pg_merchant_id": merchant_id,
            "pg_order_id": str(order_id),
            "pg_amount": f"{amount:.2f}",
            "pg_currency": currency,
            "pg_description": description[:255],
            "pg_salt": idempotency_key[:32],
            "pg_result_url": callback_url,
            "pg_success_url": success_url or callback_url,
            "pg_lifetime": str(_PAYMENT_TTL_MINUTES * 60),
        }
        if environment != "production":
            params["pg_testing_mode"] = "1"

        params["pg_sig"] = self._build_signature("init_payment.php", params, secret_key)

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_FREEDOM_PAY_API}/init_payment.php",
                data=params,
            )
            resp.raise_for_status()

        raw_text = resp.text
        payment_url = ""
        payment_id = ""
        raw_parsed: dict[str, Any] = {}

        if raw_text.strip().startswith("{"):
            raw_parsed = json.loads(raw_text)
            payment_url = str(raw_parsed.get("pg_redirect_url") or "")
            payment_id = str(raw_parsed.get("pg_payment_id") or "")
        else:
            import re
            def _xml_tag(tag: str) -> str:
                m = re.search(rf"<{tag}>(.+?)</{tag}>", raw_text, re.DOTALL)
                return m.group(1).strip() if m else ""

            payment_url = _xml_tag("pg_redirect_url")
            payment_id = _xml_tag("pg_payment_id")
            status_tag = _xml_tag("pg_status")
            raw_parsed = {"pg_status": status_tag, "raw": raw_text[:500]}

            if status_tag and status_tag != "ok":
                err = _xml_tag("pg_error_description") or status_tag
                raise ValueError(f"Freedom Pay initiation error: {err}")

        if not payment_url:
            raise ValueError(f"Freedom Pay не вернул redirect URL: {raw_text[:200]}")

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_PAYMENT_TTL_MINUTES)

        return InitiatedPayment(
            payment_url=payment_url,
            provider_payment_id=payment_id,
            expires_at=expires_at,
            raw=raw_parsed,
        )
