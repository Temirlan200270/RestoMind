"""Каркас Kaspi: подпись по контракту банка (ключ из env). Пока без ключа — verify False."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from starlette.requests import Request

from app.core.config import settings
from app.services.payment_adapters import ParsedPayment


class KaspiWebhookAdapter:
    provider_slug: ClassVar[str] = "kaspi"

    async def verify(self, request: Request, raw_body: bytes) -> bool:
        _ = request
        _ = raw_body
        # Живая интеграция: заполнить по документации Kaspi (заголовки / тело).
        _ = settings.kaspi_webhook_hmac_secret
        # Заготовка: при наличии ключа нужна реальная проверка подписи Kaspi.
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
