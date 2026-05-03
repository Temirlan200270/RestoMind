"""Регистрация платёжных адаптеров webhook."""

from __future__ import annotations

from app.services.payment_adapters import register_adapter
from app.services.payment_providers.cloudpayments import CloudPaymentsWebhookAdapter
from app.services.payment_providers.freedom_pay import FreedomPayWebhookAdapter
from app.services.payment_providers.generic_hmac import GenericHmacWebhookAdapter
from app.services.payment_providers.kaspi import KaspiWebhookAdapter


def register_payment_provider_adapters() -> None:
    register_adapter("generic_hmac", GenericHmacWebhookAdapter)
    register_adapter("cloudpayments", CloudPaymentsWebhookAdapter)
    register_adapter("kaspi", KaspiWebhookAdapter)
    register_adapter("freedom_pay", FreedomPayWebhookAdapter)


register_payment_provider_adapters()
