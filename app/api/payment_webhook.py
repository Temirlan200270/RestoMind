"""
Публичный webhook оплаты: Bearer и в prod-like окружении обязательная HMAC подпись тела.

Prod-like: ``APP_ENV`` = ``production`` | ``prod`` | ``staging``. В development достаточно Bearer или только HMAC.

Расширение под подписи банков: ``POST /api/webhooks/payment/providers/{provider_slug}`` —
для slug вне простого списка ожидается класс в ``payment_adapters.ADAPTER_REGISTRY`` (пока 501).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.services.payment_adapters import get_payment_adapter_class
from app.services.payment_autoprint_iiko import run_auto_send_to_iiko_after_payment
from app.services.payment_webhook import apply_payment_webhook

router = APIRouter(tags=["payments"])

_SIMPLE_PROVIDER_SLUGS = frozenset({"generic", "json", "bearer", "internal"})

_PAYMENT_SIG_HEADER = "x-restomind-payment-signature"


class PaymentWebhookBody(BaseModel):
    order_id: int = Field(..., ge=1)
    organization_id: int = Field(..., ge=1)
    payment_id: str = Field(..., min_length=4, max_length=200)
    status: Literal["paid", "failed"] = "paid"
    amount: float | None = Field(default=None, ge=0)
    provider: str = Field(default="generic", max_length=64)


def _payment_webhook_auth_configured() -> tuple[bool, bool]:
    bearer_ok = bool((settings.payment_webhook_bearer_token or "").strip())
    hmac_ok = bool((settings.payment_webhook_hmac_secret or "").strip())
    return bearer_ok, hmac_ok


def _extract_bearer_token(request: Request) -> str | None:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _verify_payment_webhook_request(request: Request, raw_body: bytes) -> None:
    bearer_cfg, hmac_cfg = _payment_webhook_auth_configured()
    if not bearer_cfg and not hmac_cfg:
        raise HTTPException(
            status_code=503,
            detail=(
                "Payment webhook is not configured "
                "(set PAYMENT_WEBHOOK_BEARER_TOKEN and/or PAYMENT_WEBHOOK_HMAC_SECRET)"
            ),
        )

    if settings.is_prod_like and not hmac_cfg:
        raise HTTPException(
            status_code=503,
            detail=(
                "Payment webhook: in production PAYMENT_WEBHOOK_HMAC_SECRET is required "
                "(send raw JSON body + hex HMAC-SHA256 in X-RestoMind-Payment-Signature)"
            ),
        )

    if bearer_cfg:
        expected_token = (settings.payment_webhook_bearer_token or "").strip()
        got = _extract_bearer_token(request) or ""
        if not got or not secrets.compare_digest(got, expected_token):
            raise HTTPException(status_code=401, detail="Invalid or missing Bearer token")

    if hmac_cfg:
        secret = (settings.payment_webhook_hmac_secret or "").strip().encode()
        sig_hdr = (request.headers.get(_PAYMENT_SIG_HEADER) or "").strip()
        mac = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        if not sig_hdr or not secrets.compare_digest(sig_hdr, mac):
            raise HTTPException(status_code=401, detail="Invalid payment webhook signature")


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
        await db.commit()
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
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw_body = await request.body()
    _verify_payment_webhook_request(request, raw_body)
    body = PaymentWebhookBody.model_validate_json(raw_body)
    return await _run_payment_webhook(body, db, background_tasks)


@router.post("/webhooks/payment/providers/{provider_slug}")
async def post_payment_webhook_by_provider(
    provider_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw_body = await request.body()
    _verify_payment_webhook_request(request, raw_body)
    body = PaymentWebhookBody.model_validate_json(raw_body)
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
