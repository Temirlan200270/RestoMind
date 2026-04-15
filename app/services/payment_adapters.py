"""
Адаптеры платёжных провайдеров: подпись запроса и разбор payload.

Сейчас используется общий контракт JSON + Bearer (`POST /api/webhooks/payment`).
Для прямых интеграций (Kaspi, CloudPayments, …) зарегистрируйте класс в ``ADAPTER_REGISTRY``:

    ADAPTER_REGISTRY["kaspi"] = KaspiWebhookAdapter  # должен реализовать verify + parse
"""

from __future__ import annotations

from typing import Any, Protocol

from starlette.requests import Request


class PaymentWebhookAdapter(Protocol):
    """Проверка подлинности вебхука и извлечение полей заказа."""

    async def verify(self, request: Request) -> bool:
        """HMAC / подпись провайдера."""
        ...

    async def parse(self, body: bytes) -> dict[str, Any]:
        """Нормализация к полям apply_payment_webhook (order_id, organization_id, …)."""
        ...


# Пустой реестр — заготовка для Strategy-обработчиков по пути /webhooks/payment/providers/{name}
ADAPTER_REGISTRY: dict[str, type[PaymentWebhookAdapter]] = {}


def get_payment_adapter_class(provider_slug: str) -> type[PaymentWebhookAdapter] | None:
    key = (provider_slug or "").strip().lower()
    return ADAPTER_REGISTRY.get(key)


def register_adapter(name: str, cls: type[PaymentWebhookAdapter]) -> None:
    ADAPTER_REGISTRY[(name or "").strip().lower()] = cls
