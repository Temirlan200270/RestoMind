"""QA auto-audit: deterministic risk scoring for AI-assisted orders."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AiOrderAudit, ChatLog, Order, OrderStatus, SystemEvent
from app.services.intelligence import _period_bounds
from app.services.intelligence_analytics import order_meta_from_items_json
from app.services.system_events import BusinessEvent, emit_event

logger = logging.getLogger(__name__)

AUDIT_DEBOUNCE_SEC = 90
_REDIS_AUDIT_DEBOUNCE_PREFIX = "order_ai_audit:debounce:"
_REDIS_CALIBRATION_PREFIX = "order_ai_audit:calibration:"
_CALIBRATION_STEP = 0.05
_CALIBRATION_MIN = 0.1
_CALIBRATION_MAX = 1.0
_org_calibration_memory: dict[int, dict[str, float]] = {}

AUDIT_STATUSES = frozenset({"open", "reviewed", "dismissed", "resolved"})
REVIEW_REASONS = frozenset({"no_error", "fixed", "escalated_to_manager"})
AUDIT_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"reviewed", "dismissed", "resolved"}),
    "reviewed": frozenset({"resolved", "dismissed"}),
    "dismissed": frozenset(),
    "resolved": frozenset(),
}

CONFIRMED_STATUSES = frozenset({
    OrderStatus.CONFIRMED.value,
    OrderStatus.SENDING_TO_IIKO.value,
    OrderStatus.SENT_TO_IIKO.value,
    OrderStatus.IN_TRANSIT.value,
    OrderStatus.WAITING_PICKUP.value,
    OrderStatus.COMPLETED.value,
})

MVP_RISK_TAGS = frozenset({
    "wrong_address_risk",
    "stoplist_conflict",
    "price_changed",
    "manual_edit_after_ai",
    "angry_guest",
    "low_confidence",
    "payment_risk",
    "escalation_required",
    "delivery_time_risk",
})

TAG_WEIGHTS: dict[str, int] = {
    "stoplist_conflict": 45,
    "manual_edit_after_ai": 40,
    "price_changed": 30,
    "payment_risk": 25,
    "wrong_address_risk": 20,
    "angry_guest": 20,
    "escalation_required": 20,
    "delivery_time_risk": 15,
    "low_confidence": 10,
}

RISK_LEVEL_WEIGHT: dict[str, float] = {
    "critical": 1.0,
    "high": 0.7,
    "medium": 0.35,
    "low": 0.0,
}

TAG_PROBABILITY: dict[str, float] = {
    "stoplist_conflict": 1.0,
    "manual_edit_after_ai": 0.75,
    "price_changed": 0.55,
    "payment_risk": 0.65,
    "wrong_address_risk": 0.50,
    "angry_guest": 0.45,
    "escalation_required": 0.60,
    "delivery_time_risk": 0.40,
    "low_confidence": 0.25,
}

ANGRY_KEYWORDS = (
    "ужас", "кошмар", "плохо", "отврат", "разочар", "жалоб", "жалую",
    "долго жду", "сколько ждать", "надоело", "безобраз", "верните деньги",
    "обман", "грубо", "хам", "мерз", "terrible", "disgusting", "angry",
    "wtf", "wtf?", "бесит", "возмущ",
)


def _dt_as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt_for_filter(dt: datetime) -> datetime:
    u = _dt_as_utc(dt)
    return u


def _audit_period_bounds(period: str) -> tuple[datetime, datetime]:
    start, end, _, _, _ = _period_bounds(period)
    return start, end


def _order_meta(order: Order) -> dict[str, Any]:
    items_json = order.items_json if isinstance(order.items_json, dict) else {}
    return order_meta_from_items_json(items_json)


def _is_confirmed_status(status: str | None) -> bool:
    return (status or "").strip().lower() in CONFIRMED_STATUSES


def _event_payload(ev: SystemEvent) -> dict[str, Any]:
    raw = ev.payload_json
    return raw if isinstance(raw, dict) else {}


def _confirmed_at(system_events: list[SystemEvent]) -> datetime | None:
    for ev in system_events:
        if ev.event_type == "order.confirmed":
            return ev.created_at
    return None


def _detect_tags(
    order: Order,
    chat_logs: list[ChatLog],
    system_events: list[SystemEvent],
    validated_order_context: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    """Return (tags, human-readable reasons)."""
    ctx = validated_order_context if isinstance(validated_order_context, dict) else {}
    meta = _order_meta(order)
    confidence = meta.get("confidence") if isinstance(meta.get("confidence"), dict) else {}
    tags: list[str] = []
    reasons: list[str] = []

    stoplist_items = list(ctx.get("stoplist_items") or [])
    if not stoplist_items and ctx.get("stoplist_conflict"):
        stoplist_items = list(ctx.get("stoplist_conflict_items") or ["stoplist"])
    if stoplist_items:
        tags.append("stoplist_conflict")
        reasons.append(f"Стоп-лист: {', '.join(str(x) for x in stoplist_items[:5])}")

    order_type = str(meta.get("order_type") or ctx.get("order_type") or "").strip().lower()
    delivery_address = str(meta.get("delivery_address") or ctx.get("delivery_address") or "").strip()
    if order_type == "delivery":
        if not delivery_address:
            tags.append("wrong_address_risk")
            reasons.append("Доставка без адреса")
        elif meta.get("delivery_address_verified") is not True:
            tags.append("wrong_address_risk")
            reasons.append("Адрес доставки не верифицирован")

    if confidence.get("low_confidence") is True or ctx.get("low_confidence") is True:
        tags.append("low_confidence")
        conf_reasons = confidence.get("reasons") if isinstance(confidence.get("reasons"), list) else []
        if conf_reasons:
            reasons.append(f"Низкая уверенность AI: {', '.join(str(r) for r in conf_reasons)}")
        else:
            reasons.append("Низкая уверенность AI при сборке заказа")

    for log in chat_logs:
        if (log.role or "").strip().lower() != "user":
            continue
        text = (log.content or "").lower()
        if any(kw in text for kw in ANGRY_KEYWORDS):
            tags.append("angry_guest")
            reasons.append("Негативные формулировки гостя в чате")
            break

    for ev in system_events:
        if ev.event_type in {"ai.escalated", "human_needed"}:
            tags.append("escalation_required")
            payload = _event_payload(ev)
            detail = str(payload.get("reason") or payload.get("user_message") or "").strip()
            reasons.append(f"Эскалация к оператору{(': ' + detail[:120]) if detail else ''}")
            break

    if meta.get("manual_edit_after_ai") is True or meta.get("operator_edit") is True:
        tags.append("manual_edit_after_ai")
        reasons.append("Ручная правка заказа после AI")
    else:
        for ev in system_events:
            if ev.event_type == "operator.took_over":
                tags.append("manual_edit_after_ai")
                reasons.append("Оператор перехватил диалог после AI")
                break
            actor = str(_event_payload(ev).get("_actor") or ev.source or "").lower()
            if actor in {"operator", "admin"} and ev.event_type in {
                "order.updated", "order.confirmed", "order.rebuilt",
            }:
                confirmed_at = _confirmed_at(system_events)
                if confirmed_at and ev.created_at and _dt_as_utc(ev.created_at) > _dt_as_utc(confirmed_at):
                    tags.append("manual_edit_after_ai")
                    reasons.append("Изменение заказа оператором после подтверждения")
                    break

    price_at_confirm = meta.get("price_at_confirmation")
    if price_at_confirm is not None:
        try:
            if abs(float(price_at_confirm) - float(order.total_price or 0)) > 1.0:
                tags.append("price_changed")
                reasons.append("Сумма заказа изменилась после подтверждения")
        except (TypeError, ValueError):
            pass
    elif meta.get("price_changed_after_confirm") is True:
        tags.append("price_changed")
        reasons.append("Зафиксировано изменение цены после подтверждения")
    else:
        confirmed_at = _confirmed_at(system_events)
        if confirmed_at:
            confirmed_price: float | None = None
            for ev in system_events:
                if ev.event_type != "order.confirmed":
                    continue
                payload = _event_payload(ev)
                if payload.get("total_price") is not None:
                    try:
                        confirmed_price = float(payload["total_price"])
                    except (TypeError, ValueError):
                        pass
                    break
            if confirmed_price is not None and abs(confirmed_price - float(order.total_price or 0)) > 1.0:
                tags.append("price_changed")
                reasons.append("Сумма заказа отличается от суммы при подтверждении")

    prepay = (order.prepayment_status or "").strip().lower()
    if prepay == "pending" and _is_confirmed_status(order.status):
        tags.append("payment_risk")
        reasons.append("Подтверждённый заказ с незавершённой предоплатой")
    for ev in system_events:
        if ev.event_type in {"payment.failed", "payments.failed", "integration.payment.failed"}:
            tags.append("payment_risk")
            reasons.append("Ошибка или сбой оплаты в событиях заказа")
            break

    if meta.get("delivery_time_risk") is True:
        tags.append("delivery_time_risk")
        reasons.append("Риск по сроку доставки (флаг order_meta)")
    elif order_type == "delivery" and meta.get("promised_delivery_at"):
        try:
            promised_raw = str(meta["promised_delivery_at"])
            promised_dt = datetime.fromisoformat(promised_raw.replace("Z", "+00:00"))
            if _dt_as_utc(promised_dt) < datetime.now(timezone.utc):
                tags.append("delivery_time_risk")
                reasons.append("Обещанное время доставки уже прошло")
        except (TypeError, ValueError):
            pass

    deduped_tags = [t for t in MVP_RISK_TAGS if t in tags]
    return deduped_tags, reasons


def _risk_level_from_score(score: int, tags: list[str], *, order_confirmed: bool) -> str:
    if "stoplist_conflict" in tags and order_confirmed:
        return "critical"
    if score >= 70:
        return "critical"
    if score >= 45 or "manual_edit_after_ai" in tags:
        return "high"
    if score >= 20 or ("price_changed" in tags and order_confirmed):
        return "high" if score >= 35 else "medium"
    if score >= 10 or tags:
        return "medium"
    return "low"


def score_order_ai_risk(
    order: Order,
    chat_logs: list[ChatLog],
    system_events: list[SystemEvent],
    validated_order_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic MVP risk scoring — no ML."""
    tags, reasons = _detect_tags(order, chat_logs, system_events, validated_order_context)
    score = sum(TAG_WEIGHTS.get(tag, 0) for tag in tags)
    order_confirmed = _is_confirmed_status(order.status)
    if "stoplist_conflict" in tags and order_confirmed:
        score = max(score, 70)
    risk_level = _risk_level_from_score(score, tags, order_confirmed=order_confirmed)
    return {
        "risk_score": int(score),
        "risk_level": risk_level,
        "tags": tags,
        "reasons": reasons,
    }


def _default_tag_probability(tag: str) -> float:
    return TAG_PROBABILITY.get(str(tag), 0.2)


def _probability_by_tag(
    tags: list[str],
    *,
    org_calibration: dict[str, float] | None = None,
) -> float:
    """Worst-case loss probability among detected risk tags."""
    if not tags:
        return 0.0
    cal = org_calibration or {}
    return max(cal.get(str(tag), _default_tag_probability(str(tag))) for tag in tags)


def _audit_has_tag(tag: str):
    """Cross-dialect JSON array membership for tags_json."""
    needle = f'"{str(tag).strip()}"'
    return cast(AiOrderAudit.tags_json, String).like(f"%{needle}%")


def _parse_csv_filter(raw: str | None) -> list[str]:
    return [x.strip().lower() for x in (raw or "").split(",") if x.strip()]


async def _load_org_calibration(org_id: int) -> dict[str, float]:
    cached = _org_calibration_memory.get(int(org_id))
    if cached is not None:
        return dict(cached)
    from app.db.session import redis_client

    key = f"{_REDIS_CALIBRATION_PREFIX}{int(org_id)}"
    try:
        raw = await redis_client.get(key)
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                cal = {
                    str(k): float(v)
                    for k, v in parsed.items()
                    if isinstance(v, (int, float))
                }
                _org_calibration_memory[int(org_id)] = cal
                return cal
    except Exception:
        logger.debug("audit calibration redis read failed org=%s", org_id)
    return {}


async def _save_org_calibration(org_id: int, calibration: dict[str, float]) -> None:
    cal = {str(k): round(float(v), 4) for k, v in calibration.items()}
    _org_calibration_memory[int(org_id)] = cal
    from app.db.session import redis_client

    key = f"{_REDIS_CALIBRATION_PREFIX}{int(org_id)}"
    try:
        await redis_client.set(key, json.dumps(cal))
    except Exception:
        logger.debug("audit calibration redis write failed org=%s", org_id)


async def record_audit_outcome_for_calibration(
    db: AsyncSession,
    org_id: int,
    audit: AiOrderAudit,
) -> None:
    """Persist review outcome to org-level tag calibration (memory + Redis stub)."""
    _ = db  # reserved for future DB-backed calibration rows
    reason = (audit.review_reason or "").strip().lower()
    if reason not in REVIEW_REASONS:
        return
    tags = audit.tags_json if isinstance(audit.tags_json, list) else []
    if not tags:
        return

    cal = await _load_org_calibration(org_id)
    for tag in tags:
        t = str(tag)
        base = cal.get(t, _default_tag_probability(t))
        if reason == "no_error":
            cal[t] = max(_CALIBRATION_MIN, base - _CALIBRATION_STEP)
        elif reason in {"fixed", "escalated_to_manager"}:
            cal[t] = min(_CALIBRATION_MAX, base + _CALIBRATION_STEP * 0.5)
    await _save_org_calibration(org_id, cal)


def apply_outcome_calibration(
    tags: list[str],
    review_reason: str | None = None,
    *,
    org_id: int | None = None,
    org_calibration: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Adjusted probability hints per tag after operator feedback."""
    cal = org_calibration
    if cal is None and org_id is not None:
        cal = _org_calibration_memory.get(int(org_id), {})
    cal = cal or {}
    reason = (review_reason or "").strip().lower()
    hints: dict[str, float] = {}
    for tag in tags:
        t = str(tag)
        prob = cal.get(t, _default_tag_probability(t))
        if reason == "no_error":
            prob = max(_CALIBRATION_MIN, prob - _CALIBRATION_STEP)
        elif reason in {"fixed", "escalated_to_manager"}:
            prob = min(_CALIBRATION_MAX, prob + _CALIBRATION_STEP * 0.5)
        hints[t] = round(prob, 3)
    return {
        "tag_probabilities": hints,
        "max_probability": max(hints.values()) if hints else 0.0,
        "calibration_adjusted": bool(cal),
    }


def _prevented_value(
    order: Order,
    tags: list[str],
    risk_level: str,
    *,
    org_calibration: dict[str, float] | None = None,
) -> float:
    """Prevented loss estimate: total_price × risk_weight × probability_by_tag."""
    total = float(order.total_price or 0)
    if total <= 0:
        return 0.0
    weight = RISK_LEVEL_WEIGHT.get((risk_level or "low").strip().lower(), 0.0)
    prob = _probability_by_tag(tags, org_calibration=org_calibration)
    if weight <= 0 or prob <= 0:
        return 0.0
    return round(total * weight * prob, 2)


def audit_public(row: AiOrderAudit) -> dict[str, Any]:
    tags = row.tags_json if isinstance(row.tags_json, list) else []
    reasons = row.reasons_json if isinstance(row.reasons_json, list) else []
    return {
        "id": int(row.id),
        "organization_id": int(row.organization_id),
        "location_id": int(row.location_id) if row.location_id is not None else None,
        "order_id": int(row.order_id) if row.order_id is not None else None,
        "user_id": int(row.user_id) if row.user_id is not None else None,
        "trace_id": row.trace_id,
        "risk_score": int(row.risk_score or 0),
        "risk_level": row.risk_level,
        "tags": tags,
        "reasons": reasons,
        "status": row.status,
        "prevented_value": float(row.prevented_value or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "reviewed_by_staff_id": int(row.reviewed_by_staff_id) if row.reviewed_by_staff_id is not None else None,
        "review_reason": row.review_reason,
    }


async def _load_order_context(
    db: AsyncSession,
    order: Order,
) -> tuple[list[ChatLog], list[SystemEvent], dict[str, Any]]:
    org_id = int(order.organization_id or 0)
    chat_logs = (
        await db.scalars(
            select(ChatLog)
            .where(
                ChatLog.organization_id == org_id,
                ChatLog.user_id == int(order.user_id),
            )
            .order_by(ChatLog.created_at.asc())
            .limit(200),
        )
    ).all()

    meta = _order_meta(order)
    trace_id = str(meta.get("trace_id") or "").strip()
    order_id_s = str(order.id)

    event_stmt = (
        select(SystemEvent)
        .where(SystemEvent.organization_id == org_id)
        .order_by(SystemEvent.created_at.asc())
        .limit(300)
    )
    system_events = (await db.scalars(event_stmt)).all()
    filtered: list[SystemEvent] = []
    for ev in system_events:
        if ev.entity_type == "order" and str(ev.entity_id or "") == order_id_s:
            filtered.append(ev)
            continue
        payload = _event_payload(ev)
        if str(payload.get("order_id") or "") == order_id_s:
            filtered.append(ev)
            continue
        if trace_id and str(payload.get("trace_id") or "") == trace_id:
            filtered.append(ev)
            continue
        if ev.entity_type == "user" and str(ev.entity_id or "") == str(order.user_id):
            if ev.event_type in {"ai.escalated", "human_needed", "operator.took_over"}:
                filtered.append(ev)

    validated_ctx: dict[str, Any] = {
        "order_type": meta.get("order_type"),
        "delivery_address": meta.get("delivery_address"),
        "stoplist_items": meta.get("stoplist_items") or [],
        "low_confidence": bool((meta.get("confidence") or {}).get("low_confidence"))
        if isinstance(meta.get("confidence"), dict) else False,
    }
    if isinstance(meta.get("confidence"), dict):
        validated_ctx["confidence"] = meta["confidence"]
    return list(chat_logs), filtered, validated_ctx


async def build_order_ai_audit(db: AsyncSession, order_id: int) -> AiOrderAudit:
    """Build or refresh QA audit row for an order; emit event on high/critical."""
    order = await db.get(Order, int(order_id))
    if order is None:
        raise LookupError("order_not_found")

    org_id = int(order.organization_id or 0)
    if not org_id:
        raise ValueError("order_missing_organization")

    chat_logs, system_events, validated_ctx = await _load_order_context(db, order)
    scored = score_order_ai_risk(order, chat_logs, system_events, validated_ctx)
    org_calibration = await _load_org_calibration(org_id)
    meta = _order_meta(order)
    trace_id = str(meta.get("trace_id") or "").strip() or None

    existing = await db.scalar(
        select(AiOrderAudit)
        .where(
            AiOrderAudit.organization_id == org_id,
            AiOrderAudit.order_id == int(order.id),
            AiOrderAudit.status == "open",
        )
        .order_by(AiOrderAudit.id.desc())
        .limit(1),
    )
    row = existing or AiOrderAudit(
        organization_id=org_id,
        location_id=getattr(order, "location_id", None),
        order_id=int(order.id),
        user_id=int(order.user_id),
        trace_id=trace_id,
        status="open",
    )
    row.risk_score = int(scored["risk_score"])
    row.risk_level = str(scored["risk_level"])
    row.tags_json = list(scored["tags"])
    row.reasons_json = list(scored["reasons"])
    row.prevented_value = _prevented_value(
        order,
        list(scored["tags"]),
        str(scored["risk_level"]),
        org_calibration=org_calibration,
    )
    if existing is None:
        db.add(row)
    await db.flush()

    if scored["risk_level"] in {"high", "critical"}:
        await emit_event(
            db,
            BusinessEvent(
                org_id=org_id,
                type="ai_order.audit_risk_detected",
                actor="system",
                location_id=getattr(order, "location_id", None),
                entity_type="order",
                entity_id=int(order.id),
                id=f"ai_order.audit_risk:{row.id}",
                payload={
                    "audit_id": int(row.id),
                    "order_id": int(order.id),
                    "risk_score": int(scored["risk_score"]),
                    "risk_level": str(scored["risk_level"]),
                    "tags": list(scored["tags"]),
                    "prevented_value": float(row.prevented_value or 0),
                    "trace_id": trace_id,
                },
            ),
        )
    return row


async def list_order_ai_audits(
    db: AsyncSession,
    org_id: int,
    *,
    status: str | None = "open",
    period: str = "today",
    location_id: int | None = None,
    order_id: int | None = None,
    risk_level: str | None = None,
    tags: str | None = None,
    unreviewed_only: bool = False,
    allowed_location_ids: set[int] | None = None,
    limit: int = 100,
) -> list[AiOrderAudit]:
    start, end, _, _, _ = _period_bounds(period)
    stmt = (
        select(AiOrderAudit)
        .where(
            AiOrderAudit.organization_id == int(org_id),
            AiOrderAudit.created_at >= _sql_dt_for_filter(start),
            AiOrderAudit.created_at <= _sql_dt_for_filter(end),
        )
        .order_by(AiOrderAudit.risk_score.desc(), AiOrderAudit.created_at.desc())
        .limit(max(1, min(int(limit), 200)))
    )
    if unreviewed_only:
        stmt = stmt.where(AiOrderAudit.status == "open")
    else:
        st = (status or "").strip().lower()
        if st and st != "all":
            if st not in AUDIT_STATUSES:
                st = "open"
            stmt = stmt.where(AiOrderAudit.status == st)
    if order_id is not None:
        stmt = stmt.where(AiOrderAudit.order_id == int(order_id))
    risk_filter = _parse_csv_filter(risk_level)
    if risk_filter:
        stmt = stmt.where(AiOrderAudit.risk_level.in_(risk_filter))
    tag_filter = _parse_csv_filter(tags)
    if tag_filter:
        stmt = stmt.where(or_(*[_audit_has_tag(tag) for tag in tag_filter]))
    if location_id is not None:
        stmt = stmt.where(AiOrderAudit.location_id == int(location_id))
    elif allowed_location_ids is not None:
        stmt = stmt.where(AiOrderAudit.location_id.in_(list(allowed_location_ids)))
    return list((await db.scalars(stmt)).all())


async def summarize_order_ai_audits(
    db: AsyncSession,
    org_id: int,
    *,
    status: str | None = "open",
    period: str = "today",
    location_id: int | None = None,
    order_id: int | None = None,
    risk_level: str | None = None,
    tags: str | None = None,
    unreviewed_only: bool = False,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Aggregate audit counts for the QA manager filters."""
    rows = await list_order_ai_audits(
        db,
        org_id,
        status=status,
        period=period,
        location_id=location_id,
        order_id=order_id,
        risk_level=risk_level,
        tags=tags,
        unreviewed_only=unreviewed_only,
        allowed_location_ids=allowed_location_ids,
        limit=500,
    )
    open_count = sum(1 for r in rows if (r.status or "") == "open")
    reviewed_count = sum(1 for r in rows if (r.status or "") == "reviewed")
    dismissed_count = sum(1 for r in rows if (r.status or "") == "dismissed")
    resolved_count = sum(1 for r in rows if (r.status or "") == "resolved")
    high_count = sum(1 for r in rows if (r.risk_level or "") == "high")
    critical_count = sum(1 for r in rows if (r.risk_level or "") == "critical")
    tag_counts: dict[str, int] = {}
    for row in rows:
        row_tags = row.tags_json if isinstance(row.tags_json, list) else []
        for tag in row_tags:
            tag_counts[str(tag)] = tag_counts.get(str(tag), 0) + 1
    return {
        "total": len(rows),
        "open_count": open_count,
        "reviewed_count": reviewed_count,
        "dismissed_count": dismissed_count,
        "resolved_count": resolved_count,
        "high_count": high_count,
        "critical_count": critical_count,
        "unreviewed_count": open_count,
        "top_tags": [{"tag": t, "count": c} for t, c in sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))[:8]],
    }


async def mark_order_ai_audit_status(
    db: AsyncSession,
    audit_id: int,
    org_id: int,
    status: str,
    staff_id: int | None,
    *,
    review_reason: str | None = None,
) -> AiOrderAudit:
    row = await db.get(AiOrderAudit, int(audit_id))
    if row is None or int(row.organization_id) != int(org_id):
        raise LookupError("audit_not_found")

    new_status = (status or "").strip().lower()
    if new_status not in AUDIT_STATUSES:
        raise ValueError(f"invalid_status:{new_status}")

    cur = (row.status or "open").strip().lower()
    allowed = AUDIT_STATUS_TRANSITIONS.get(cur, frozenset())
    if new_status != cur and new_status not in allowed:
        raise ValueError(f"invalid_transition:{cur}->{new_status}")

    row.status = new_status
    if new_status in {"reviewed", "resolved", "dismissed"}:
        row.reviewed_at = datetime.now(timezone.utc)
        if staff_id is not None:
            row.reviewed_by_staff_id = int(staff_id)
    if review_reason is not None:
        reason = (review_reason or "").strip().lower()
        if reason and reason not in REVIEW_REASONS:
            raise ValueError(f"invalid_review_reason:{reason}")
        row.review_reason = reason or None
    await db.flush()
    if new_status == "reviewed":
        await record_audit_outcome_for_calibration(db, org_id, row)
    return row


async def _audit_debounce_acquire(org_id: int, order_id: int) -> bool:
    """True if this call should run audit (first within debounce window)."""
    from app.db.session import redis_client

    key = f"{_REDIS_AUDIT_DEBOUNCE_PREFIX}{int(org_id)}:{int(order_id)}"
    try:
        ok = await redis_client.set(key, "1", nx=True, ex=AUDIT_DEBOUNCE_SEC)  # type: ignore[call-arg]
        return bool(ok)
    except TypeError:
        prev = await redis_client.get(key)
        if prev:
            return False
        await redis_client.set(key, "1")
        return True
    except Exception:
        logger.debug("audit debounce redis unavailable org=%s order=%s", org_id, order_id)
        return True


async def maybe_audit_risky_draft_update(
    db: AsyncSession,
    order: Order,
) -> AiOrderAudit | None:
    """Debounced QA audit after draft mutation when risk tags are present."""
    if order.id is None or order.organization_id is None:
        return None
    org_id = int(order.organization_id)
    order_id = int(order.id)
    if not await _audit_debounce_acquire(org_id, order_id):
        return None

    chat_logs, system_events, validated_ctx = await _load_order_context(db, order)
    scored = score_order_ai_risk(order, chat_logs, system_events, validated_ctx)
    if not scored.get("tags") and str(scored.get("risk_level") or "low") == "low":
        return None

    try:
        return await build_order_ai_audit(db, order_id)
    except Exception as exc:
        logger.debug("maybe_audit_risky_draft_update order=%s: %s", order_id, exc)
        return None


async def build_qa_risk_summary(
    db: AsyncSession,
    org_id: int,
    *,
    ts_lo: datetime,
    ts_hi: datetime,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Aggregate open/high/critical counts for Owner Intelligence summary."""
    base = (
        select(AiOrderAudit)
        .where(
            AiOrderAudit.organization_id == int(org_id),
            AiOrderAudit.created_at >= _sql_dt_for_filter(ts_lo),
            AiOrderAudit.created_at <= _sql_dt_for_filter(ts_hi),
        )
    )
    if location_id is not None:
        base = base.where(AiOrderAudit.location_id == int(location_id))
    elif allowed_location_ids is not None:
        base = base.where(AiOrderAudit.location_id.in_(list(allowed_location_ids)))

    rows = list((await db.scalars(base)).all())
    open_count = sum(1 for r in rows if (r.status or "") == "open")
    closed_count = sum(
        1 for r in rows if (r.status or "") in {"reviewed", "dismissed", "resolved"}
    )
    high_count = sum(1 for r in rows if (r.risk_level or "") == "high")
    critical_count = sum(1 for r in rows if (r.risk_level or "") == "critical")
    tag_counts: dict[str, int] = {}
    for r in rows:
        tags = r.tags_json if isinstance(r.tags_json, list) else []
        for tag in tags:
            tag_counts[str(tag)] = tag_counts.get(str(tag), 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))[:5]

    return {
        "open_count": open_count,
        "closed_count": closed_count,
        "high_count": high_count,
        "critical_count": critical_count,
        "ready": open_count > 0,
        "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
    }


async def backfill_order_ai_audits(
    db: AsyncSession,
    org_id: int,
    *,
    period: str = "today",
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
    limit: int = 200,
) -> dict[str, int]:
    """Пересчитать QA-аудиты для подтверждённых заказов за период (cron / ручной запуск)."""
    from app.db.models import Order, OrderStatus

    start, end = _audit_period_bounds(period)
    q = (
        select(Order.id)
        .where(
            Order.organization_id == int(org_id),
            Order.status.in_(
                [
                    OrderStatus.CONFIRMED.value,
                    OrderStatus.SENT_TO_IIKO.value,
                    OrderStatus.IN_TRANSIT.value,
                    OrderStatus.WAITING_PICKUP.value,
                    OrderStatus.COMPLETED.value,
                ],
            ),
            Order.updated_at >= _sql_dt_for_filter(start),
            Order.updated_at < _sql_dt_for_filter(end),
        )
        .order_by(Order.updated_at.desc())
        .limit(max(1, min(int(limit), 500)))
    )
    if location_id is not None:
        q = q.where(Order.location_id == int(location_id))
    elif allowed_location_ids is not None:
        q = q.where(Order.location_id.in_(list(allowed_location_ids)))

    order_ids = [int(x) for x in (await db.scalars(q)).all()]
    processed = 0
    high_or_critical = 0
    for oid in order_ids:
        try:
            row = await build_order_ai_audit(db, oid)
            processed += 1
            if (row.risk_level or "") in {"high", "critical"}:
                high_or_critical += 1
        except Exception as exc:
            logger.debug("backfill audit skip order=%s: %s", oid, exc)
    return {
        "processed": processed,
        "high_or_critical": high_or_critical,
        "candidates": len(order_ids),
    }
