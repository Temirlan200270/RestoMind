"""
Протоколы для инициации платежей и разбора webhook.

PaymentInitiator — создаёт платёжную сессию у провайдера и возвращает URL.
Каждый провайдер реализует оба протокола: WebhookAdapter + Initiator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class InitiatedPayment:
    """Результат вызова create_payment у провайдера."""

    payment_url: str
    provider_payment_id: str
    expires_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentInitiator(Protocol):
    """Инициирует платёж у провайдера и возвращает ссылку для клиента."""

    provider_slug: str

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
        """
        Создаёт платёжную сессию.

        credentials — расшифрованные поля из OrganizationPaymentConfig:
            api_key, secret_key, merchant_id, public_key, extra (dict)
        """
        ...
