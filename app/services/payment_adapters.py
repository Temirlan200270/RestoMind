"""
Адаптеры платёжных провайдеров: подпись запроса и разбор payload.

Сейчас используется общий контракт JSON + Bearer (`POST /api/webhooks/payment`).
Для прямых интеграций (Kaspi, CloudPayments, …) зарегистрируйте класс в ``ADAPTER_REGISTRY``:

    ADAPTER_REGISTRY["kaspi"] = KaspiWebhookAdapter  # должен реализовать verify + parse
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from starlette.requests import Request

PaymentParseStatus = Literal["paid", "failed"]


@dataclass(frozen=True)
class ParsedPayment:
    """Нормализованный результат разбора тела webhook провайдера."""

    order_id: int
    organization_id: int
    payment_id: str
    status: PaymentParseStatus
    amount: float | None
    raw: dict[str, Any]


class PaymentWebhookAdapter(Protocol):
    """Проверка подлинности вебхука и извлечение полей заказа."""

    provider_slug: str

    async def verify(self, request: Request, raw_body: bytes) -> bool:
        """HMAC / подпись провайдера (сырое тело POST)."""
        ...

    async def parse(self, raw_body: bytes) -> ParsedPayment:
        """Нормализация к полям apply_payment_webhook."""
        ...


# Пустой реестр — заготовка для Strategy-обработчиков по пути /webhooks/payment/providers/{name}
ADAPTER_REGISTRY: dict[str, type[PaymentWebhookAdapter]] = {}


def get_payment_adapter_class(provider_slug: str) -> type[PaymentWebhookAdapter] | None:
    key = (provider_slug or "").strip().lower()
    return ADAPTER_REGISTRY.get(key)


def register_adapter(name: str, cls: type[PaymentWebhookAdapter]) -> None:
    ADAPTER_REGISTRY[(name or "").strip().lower()] = cls
