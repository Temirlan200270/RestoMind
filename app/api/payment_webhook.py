"""
Публичный webhook оплаты: Bearer и в prod-like окружении обязательная HMAC подпись тела.

Расширение: POST /api/webhooks/payment/providers/{provider_slug} — адаптеры из payment_providers.
Аудит сырых запросов: таблица payment_webhook_events.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.payment_providers  # noqa: F401 — регистрация ADAPTER_REGISTRY
from app.core.config import settings
from app.db.models import PaymentWebhookEvent
from app.db.session import get_db
from app.services.payment_adapters import ParsedPayment, get_payment_adapter_class
from app.services.payment_autoprint_iiko import run_auto_send_to_iiko_after_payment
from app.services.payment_webhook import apply_payment_webhook
from app.services.payment_webhook_audit import (
    insert_payment_webhook_audit,
    update_payment_webhook_audit,
)

router = APIRouter(tags=["payments"])

_SIMPLE_PROVIDER_SLUGS = frozenset({"generic", "json", "bearer", "internal"})
_PAYMENT_SIG_HEADER = "x-restomind-payment-signature"
_RESTOMIND_PROVIDER_SLUG = "restomind_json"
_MAX_JSON_PARSE_BYTES = 512 * 1024


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


def _parsed_to_body(parsed: ParsedPayment, provider_field: str) -> PaymentWebhookBody:
    return PaymentWebhookBody(
        order_id=parsed.order_id,
        organization_id=parsed.organization_id,
        payment_id=parsed.payment_id,
        status=parsed.status,
        amount=parsed.amount,
        provider=provider_field,
    )


async def _run_payment_webhook(
    body: PaymentWebhookBody,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    audit_event: PaymentWebhookEvent | None,
    raw_payload: dict | None = None,
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
            raw_payload=raw_payload,
        )
    except LookupError:
        if audit_event is not None:
            await update_payment_webhook_audit(
                db,
                audit_event,
                verified=True,
                verify_error="order_not_found",
                applied=False,
            )
            await db.commit()
        raise HTTPException(status_code=404, detail="Order not found") from None
    except PermissionError:
        if audit_event is not None:
            await update_payment_webhook_audit(
                db,
                audit_event,
                verified=True,
                verify_error="organization_mismatch",
                applied=False,
            )
        await db.commit()
        raise HTTPException(status_code=403, detail="organization_id does not match order") from None
    except ValueError as exc:
        if audit_event is not None:
            await update_payment_webhook_audit(
                db,
                audit_event,
                verified=True,
                verify_error=str(exc)[:500],
                applied=False,
            )
            await db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from None

    if audit_event is not None:
        ext_id = body.payment_id.strip()[:200]
        await update_payment_webhook_audit(
            db,
            audit_event,
            verified=True,
            organization_id=body.organization_id,
            order_id=body.order_id,
            external_payment_id=ext_id,
            parsed_status=body.status,
            parsed_amount=float(body.amount) if body.amount is not None else None,
            applied=True,
            duplicate=bool(out.get("duplicate")),
            payment_event_id=out.get("payment_event_id"),
            verify_error=(audit_event.verify_error or "")[:2000],
        )

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


async def _finalize_verify_failure(
    db: AsyncSession,
    audit_event: PaymentWebhookEvent | None,
    detail: str,
) -> None:
    if audit_event is None:
        return
    await update_payment_webhook_audit(
        db,
        audit_event,
        verified=False,
        verify_error=detail[:4000],
        applied=False,
    )
    await db.commit()


@router.post("/webhooks/payment")
async def post_payment_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw_full = await request.body()
    if len(raw_full) > _MAX_JSON_PARSE_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    audit_event = await insert_payment_webhook_audit(
        db,
        provider_slug=_RESTOMIND_PROVIDER_SLUG,
        raw_body=raw_full,
        request=request,
    )
    await db.flush()

    try:
        _verify_payment_webhook_request(request, raw_full)
    except HTTPException as exc:
        detail = str(exc.detail) if isinstance(exc.detail, str) else repr(exc.detail)
        await _finalize_verify_failure(db, audit_event, detail)
        raise

    try:
        body = PaymentWebhookBody.model_validate_json(raw_full)
    except (json.JSONDecodeError, PydanticValidationError, UnicodeDecodeError) as exc:
        await _finalize_verify_failure(db, audit_event, f"invalid_body:{exc!s}")
        raise HTTPException(status_code=422, detail="Invalid JSON body") from exc

    await update_payment_webhook_audit(db, audit_event, verified=True)
    await db.flush()

    return await _run_payment_webhook(body, db, background_tasks, audit_event)


@router.post("/webhooks/payment/providers/{provider_slug}")
async def post_payment_webhook_by_provider(
    provider_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw_full = await request.body()
    if len(raw_full) > _MAX_JSON_PARSE_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    slug = (provider_slug or "").strip().lower()
    audit_event = await insert_payment_webhook_audit(
        db,
        provider_slug=slug,
        raw_body=raw_full,
        request=request,
    )
    await db.flush()

    adapter_cls = get_payment_adapter_class(slug)
    if adapter_cls is not None:
        adapter = adapter_cls()
        ok = await adapter.verify(request, raw_full)
        if not ok:
            await _finalize_verify_failure(db, audit_event, "adapter_verify_failed")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        try:
            parsed = await adapter.parse(raw_full)
        except (json.JSONDecodeError, ValueError, KeyError, PydanticValidationError) as exc:
            await _finalize_verify_failure(db, audit_event, f"adapter_parse:{exc!s}")
            raise HTTPException(status_code=422, detail="Invalid provider payload") from exc

        body = _parsed_to_body(parsed, slug)
        await update_payment_webhook_audit(db, audit_event, verified=True)
        await db.flush()
        return await _run_payment_webhook(body, db, background_tasks, audit_event, raw_payload=parsed.raw)

    if slug not in _SIMPLE_PROVIDER_SLUGS:
        await _finalize_verify_failure(db, audit_event, "unknown_provider")
        raise HTTPException(
            status_code=404,
            detail=f"Unknown payment provider '{provider_slug}'",
        )

    try:
        _verify_payment_webhook_request(request, raw_full)
    except HTTPException as exc:
        detail = str(exc.detail) if isinstance(exc.detail, str) else repr(exc.detail)
        await _finalize_verify_failure(db, audit_event, detail)
        raise

    try:
        body = PaymentWebhookBody.model_validate_json(raw_full)
    except (json.JSONDecodeError, PydanticValidationError, UnicodeDecodeError) as exc:
        await _finalize_verify_failure(db, audit_event, f"invalid_body:{exc!s}")
        raise HTTPException(status_code=422, detail="Invalid JSON body") from exc

    if not (body.provider or "").strip() or (body.provider or "").strip().lower() == "generic":
        body = body.model_copy(update={"provider": slug})

    await update_payment_webhook_audit(db, audit_event, verified=True)
    await db.flush()

    return await _run_payment_webhook(body, db, background_tasks, audit_event)
