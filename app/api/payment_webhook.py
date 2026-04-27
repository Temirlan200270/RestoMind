"""
Публичный webhook оплаты: Bearer-токен в Authorization, идемпотентность по provider+payment_id.

Расширение под подписи банков: ``POST /api/webhooks/payment/providers/{provider_slug}`` —
для slug вне простого списка ожидается класс в ``payment_adapters.ADAPTER_REGISTRY`` (пока 501).
"""

from __future__ import annotations

import secrets
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.services.payment_adapters import get_payment_adapter_class
from app.services.payment_autoprint_iiko import run_auto_send_to_iiko_after_payment
from app.services.payment_webhook import apply_payment_webhook

router = APIRouter(tags=["payments"])

_SIMPLE_PROVIDER_SLUGS = frozenset({"generic", "json", "bearer", "internal"})
_bearer_scheme = HTTPBearer(auto_error=False)


class PaymentWebhookBody(BaseModel):
    order_id: int = Field(..., ge=1)
    organization_id: int = Field(..., ge=1)
    payment_id: str = Field(..., min_length=4, max_length=200)
    status: Literal["paid", "failed"] = "paid"
    amount: float | None = Field(default=None, ge=0)
    provider: str = Field(default="generic", max_length=64)


def verify_webhook_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    expected_token = (settings.payment_webhook_bearer_token or "").strip()
    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="Payment webhook is not configured (set PAYMENT_WEBHOOK_BEARER_TOKEN)",
        )
    if creds is None or not (creds.credentials or "").strip():
        raise HTTPException(status_code=401, detail="Expected Authorization: Bearer <token>")
    got = (creds.credentials or "").strip()
    if not secrets.compare_digest(got, expected_token):
        raise HTTPException(status_code=401, detail="Invalid token")


async def _run_payment_webhook(
    body: PaymentWebhookBody,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> dict:
    try:
        out = await apply_payment_webhook(
            db,
            order_id=body.order_id,
            organization_id=body.organization_id,
            payment_id=body.payment_id.strip(),
            provider=body.provider,
            status=body.status,
            amount=body.amount,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Order not found") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="organization_id does not match order") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    should_notify = (
        out.get("ok")
        and not out.get("duplicate")
        and body.status == "paid"
        and (out.get("prepayment_status") or "").strip().lower() == "paid"
    )

    await db.commit()

    if should_notify:
        from app.services.task_queue import dispatch_arq_or_background

        await dispatch_arq_or_background(
            "payment_notify_customer",
            background_tasks,
            order_id=int(body.order_id),
        )
        background_tasks.add_task(run_auto_send_to_iiko_after_payment, int(body.order_id))
    return out


@router.post("/webhooks/payment")
async def post_payment_webhook(
    body: PaymentWebhookBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_webhook_token),
) -> dict:
    return await _run_payment_webhook(body, db, background_tasks)


@router.post("/webhooks/payment/providers/{provider_slug}")
async def post_payment_webhook_by_provider(
    provider_slug: str,
    body: PaymentWebhookBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_webhook_token),
) -> dict:
    slug = (provider_slug or "").strip().lower()
    adapter_cls = get_payment_adapter_class(slug)
    if adapter_cls is not None:
        raise HTTPException(
            status_code=501,
            detail=f"Signed webhook adapter '{provider_slug}' is registered but not wired in this build",
        )
    if slug not in _SIMPLE_PROVIDER_SLUGS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown payment provider '{provider_slug}'",
        )
    if not (body.provider or "").strip() or (body.provider or "").strip().lower() == "generic":
        body.provider = provider_slug
    return await _run_payment_webhook(body, db, background_tasks)
