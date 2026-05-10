"""Регистрация платёжных адаптеров (webhook) и инициаторов (create_payment)."""

from __future__ import annotations

from app.services.payment_adapters import register_adapter
from app.services.payment_providers.cloudpayments import CloudPaymentsWebhookAdapter
from app.services.payment_providers.freedom_pay import FreedomPayInitiator, FreedomPayWebhookAdapter
from app.services.payment_providers.generic_hmac import GenericHmacWebhookAdapter
from app.services.payment_providers.kaspi import KaspiWebhookAdapter
from app.services.payment_providers.base import PaymentInitiator

_INITIATOR_REGISTRY: dict[str, PaymentInitiator] = {}


def register_payment_provider_adapters() -> None:
    register_adapter("generic_hmac", GenericHmacWebhookAdapter)
    register_adapter("cloudpayments", CloudPaymentsWebhookAdapter)
    register_adapter("kaspi", KaspiWebhookAdapter)
    register_adapter("freedom_pay", FreedomPayWebhookAdapter)

    _INITIATOR_REGISTRY["freedom_pay"] = FreedomPayInitiator()


def get_initiator(provider_slug: str) -> PaymentInitiator | None:
    """Возвращает инициатор для провайдера или None если не поддерживается."""
    return _INITIATOR_REGISTRY.get((provider_slug or "").strip().lower())


register_payment_provider_adapters()
