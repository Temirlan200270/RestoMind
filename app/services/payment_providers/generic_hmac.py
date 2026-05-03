"""
Адаптер generic_hmac: JSON как у RestoMind, подпись X-Signature-256 = hex(HMAC-SHA256(secret, body)).
Секрет: PAYMENT_WEBHOOK_HMAC_SECRET.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import ClassVar

from starlette.requests import Request

from app.core.config import settings
from app.services.payment_adapters import ParsedPayment


class GenericHmacWebhookAdapter:
    provider_slug: ClassVar[str] = "generic_hmac"

    async def verify(self, request: Request, raw_body: bytes) -> bool:
        secret = (settings.payment_webhook_hmac_secret or "").strip().encode()
        if not secret:
            return False
        sig_hdr = (request.headers.get("x-signature-256") or "").strip().lower()
        if not sig_hdr:
            return False
        mac = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        return secrets.compare_digest(sig_hdr, mac)

    async def parse(self, raw_body: bytes) -> ParsedPayment:
        data = json.loads(raw_body.decode("utf-8"))
        oid = int(data["order_id"])
        gid = int(data["organization_id"])
        pid = str(data["payment_id"]).strip()
        status_raw = (data.get("status") or "paid").strip().lower()
        status = "failed" if status_raw == "failed" else "paid"
        amt = data.get("amount")
        amount_f = float(amt) if amt is not None else None
        return ParsedPayment(
            order_id=oid,
            organization_id=gid,
            payment_id=pid,
            status=status,
            amount=amount_f,
            raw=data if isinstance(data, dict) else {},
        )
