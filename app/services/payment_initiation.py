"""
Payment Initiation Service — создаёт PaymentTransaction и получает URL у провайдера.

Паттерн:
    order = await db.get(Order, order_id)
    tx = await initiate_payment(db, order=order, org=org)
    # tx.payment_url — отправить клиенту в WhatsApp

Новая ссылка = новая PaymentTransaction (re-initiation после expired — тоже новая запись).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrganizationPaymentConfig, PaymentTransaction, PaymentTxStatus
from app.services.secrets_crypto import decrypt_secret

if TYPE_CHECKING:
    from app.db.models import Organization


class PaymentInitiationError(Exception):
    """Ошибка при создании платёжной сессии у провайдера."""


class NoPaymentConfigError(PaymentInitiationError):
    """Для организации не настроен платёжный провайдер."""


def _build_idempotency_key(order_id: int, provider: str) -> str:
    return f"pay:{provider}:{order_id}:{uuid.uuid4().hex[:12]}"


async def _load_primary_config(
    db: AsyncSession,
    organization_id: int,
) -> OrganizationPaymentConfig:
    row = await db.scalar(
        select(OrganizationPaymentConfig)
        .where(
            OrganizationPaymentConfig.organization_id == organization_id,
            OrganizationPaymentConfig.is_enabled.is_(True),
            OrganizationPaymentConfig.is_primary.is_(True),
        )
        .limit(1)
    )
    if row is None:
        raise NoPaymentConfigError(
            f"Нет активного платёжного провайдера для org={organization_id}"
        )
    return row


def _decrypt_credentials(cfg: OrganizationPaymentConfig) -> dict[str, str]:
    """Расшифровывает поля конфига. Незаполненные поля → пустая строка."""

    def _d(enc: str | None) -> str:
        if not enc:
            return ""
        return decrypt_secret(enc)

    extra = cfg.extra_config_json or {}
    return {
        "api_key": _d(cfg.encrypted_api_key),
        "secret_key": _d(cfg.encrypted_secret_key),
        "public_key": _d(cfg.encrypted_public_key),
        "merchant_id": cfg.merchant_id or "",
        "environment": str(extra.get("environment", "production")),
        "callback_url": str(extra.get("callback_url", "")),
        "success_url": str(extra.get("success_url", "")),
    }


async def initiate_payment(
    db: AsyncSession,
    *,
    order: Order,
    org: "Organization",
    description: str = "",
) -> PaymentTransaction:
    """
    Создаёт PaymentTransaction и вызывает API провайдера.

    Возвращает сохранённую транзакцию с заполненным payment_url.
    При ошибке провайдера — сохраняет транзакцию со status=failed и пробрасывает исключение.
    """
    from app.services.payment_providers import get_initiator  # avoid circular

    cfg = await _load_primary_config(db, org.id)
    credentials = _decrypt_credentials(cfg)

    provider_slug = cfg.provider
    initiator = get_initiator(provider_slug)
    if initiator is None:
        raise PaymentInitiationError(
            f"Провайдер '{provider_slug}' не поддерживает инициацию платежей"
        )

    idempotency_key = _build_idempotency_key(order.id, provider_slug)
    amount = float(order.total_price or 0)
    currency = org.currency or "KZT"
    desc = description or f"Заказ #{order.id}"

    callback_url = credentials["callback_url"]
    success_url = credentials["success_url"]

    tx = PaymentTransaction(
        organization_id=org.id,
        order_id=order.id,
        provider=provider_slug,
        status=PaymentTxStatus.PENDING.value,
        amount=amount,
        currency=currency,
        idempotency_key=idempotency_key,
        metadata_json={"description": desc},
    )
    db.add(tx)
    await db.flush()  # получаем tx.id до вызова провайдера

    try:
        result = await initiator.create_payment(
            order_id=order.id,
            amount=amount,
            currency=currency,
            description=desc,
            idempotency_key=idempotency_key,
            callback_url=callback_url,
            success_url=success_url,
            credentials=credentials,
        )
    except Exception as exc:
        tx.status = PaymentTxStatus.FAILED.value
        tx.failure_reason = str(exc)[:512]
        await db.flush()
        raise PaymentInitiationError(str(exc)) from exc

    tx.payment_url = result.payment_url
    tx.provider_payment_id = result.provider_payment_id or None
    tx.expires_at = result.expires_at
    tx.provider_payload_json = result.raw or None

    # Совместимость: обновляем Order для текущего UI
    order.payment_link_url = result.payment_url
    order.payment_provider = provider_slug
    order.prepayment_status = "pending"

    await db.flush()
    return tx


async def re_initiate_payment(
    db: AsyncSession,
    *,
    order: Order,
    org: "Organization",
    description: str = "",
) -> PaymentTransaction:
    """
    Повторная генерация ссылки (после expired или failed).
    Предыдущая транзакция остаётся в БД, создаётся новая.
    """
    return await initiate_payment(db, order=order, org=org, description=description)


async def mark_payment_waived(
    db: AsyncSession,
    *,
    order: Order,
    actor: str = "admin",
) -> PaymentTransaction | None:
    """Ручное снятие требования предоплаты (оператор подтвердил вручную)."""
    tx = PaymentTransaction(
        organization_id=int(order.organization_id or 0),
        order_id=order.id,
        provider="manual",
        status=PaymentTxStatus.WAIVED.value,
        amount=float(order.total_price or 0),
        currency="KZT",
        idempotency_key=f"waived:{order.id}:{actor}:{uuid.uuid4().hex[:8]}",
        metadata_json={"actor": actor},
        paid_at=datetime.now(timezone.utc),
    )
    db.add(tx)
    order.prepayment_status = "waived"
    await db.flush()
    return tx
