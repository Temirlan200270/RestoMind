"""
Business recommendations engine (P4 sprint).

Генерирует детерминированные рекомендации для ресторана на основе
существующих аналитических данных (без LLM). Запускается фоновой задачей
раз в сутки (UTC 04:00) или вручную через API.

Типы рекомендаций:
  product_boost  — блюдо часто конвертирует, стоит продвигать
  pricing_adj    — блюдо предлагается много, но конверсия низкая → снизить цену?
  geo_expansion  — адресный сегмент с высоким повторным заказом
  stoplist_impact — блюдо часто в стоп-листе → проблема с поставкой
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BusinessRecommendation, Order, OrderStatus, SystemEvent
from app.services.intelligence_analytics import (
    delivery_geo_rows,
    menu_engineering_rows,
)
from app.services.tenant_scope import orders_tenant_clause

logger = logging.getLogger(__name__)

_MIN_OFFER_COUNT = 8       # минимум предложений для статистики
_BOOST_CONV_THRESHOLD = 40  # % конверсии для product_boost
_LOW_CONV_THRESHOLD = 15    # % конверсии для pricing_adj
_GEO_LOYALTY_THRESHOLD = 1.8  # повторных заказов/клиент


async def _load_orders(db: AsyncSession, org_id: int, days: int) -> list[Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    from app.services.intelligence import _sql_dt_for_filter
    since_sql = _sql_dt_for_filter(since)
    rows = (await db.execute(
        select(Order).where(
            orders_tenant_clause(org_id),
            Order.status != OrderStatus.CANCELLED.value,
            Order.created_at >= since_sql,
        ).limit(2000)
    )).scalars().all()
    return list(rows)


async def _upsert_recommendation(
    db: AsyncSession,
    org_id: int,
    rec_type: str,
    title: str,
    body: str,
    confidence_pct: int,
    expected_impact_kzt: int | None,
    data: dict,
) -> BusinessRecommendation | None:
    """Вставляет рекомендацию если её ещё нет (дедупликация по org+type+title за 7 дней)."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    existing = await db.scalar(
        select(BusinessRecommendation).where(
            BusinessRecommendation.organization_id == org_id,
            BusinessRecommendation.recommendation_type == rec_type,
            BusinessRecommendation.title == title,
            BusinessRecommendation.created_at >= since,
        ).limit(1)
    )
    if existing:
        return None
    rec = BusinessRecommendation(
        organization_id=org_id,
        recommendation_type=rec_type,
        title=title,
        body=body,
        confidence_pct=max(0, min(100, confidence_pct)),
        expected_impact_kzt=expected_impact_kzt,
        status="new",
        data_json=data,
    )
    db.add(rec)
    await db.flush()
    return rec


async def _generate_event_driven_recommendations(
    db: AsyncSession,
    org_id: int,
    days: int = 7,
) -> list[BusinessRecommendation]:
    """Рекомендации из DailyOrgStats — не требуют чтения Order.

    Типы: cancellation_surge (высокий уровень отмен), revenue_dip (падение выручки),
    peak_load (перегрузка), low_conversion (низкая конверсия диалог→заказ).
    """
    from app.services.analytics_consumer import get_event_stats
    rows = await get_event_stats(db, org_id, days=max(days, 14))
    if not rows:
        return []

    generated: list[BusinessRecommendation] = []
    recent = rows[:days]  # последние N дней (rows отсортированы DESC)
    prev = rows[days:days * 2]  # предыдущий период для сравнения

    # ── Cancellation surge ────────────────────────────────────────────────
    recent_total = sum(r["orders_confirmed"] + r["orders_cancelled"] for r in recent)
    recent_cancelled = sum(r["orders_cancelled"] for r in recent)
    if recent_total > 0:
        cancel_rate = recent_cancelled / recent_total
        if cancel_rate >= 0.15:
            rec = await _upsert_recommendation(
                db, org_id,
                rec_type="cancellation_surge",
                title=f"Высокий уровень отмен: {cancel_rate * 100:.0f}%",
                body=(
                    f"За последние {days} дней отменено {recent_cancelled} заказов из {recent_total} "
                    f"({cancel_rate * 100:.0f}%). Рекомендуется выяснить причины: "
                    "проблемы с доставкой, долгое ожидание подтверждения или несоответствие ожиданий."
                ),
                confidence_pct=min(90, 60 + int(cancel_rate * 100)),
                expected_impact_kzt=None,
                data={"cancel_rate_pct": round(cancel_rate * 100, 1), "total_orders": recent_total},
            )
            if rec:
                generated.append(rec)

    # ── Revenue dip ───────────────────────────────────────────────────────
    recent_revenue = sum(r["revenue_kzt"] for r in recent)
    prev_revenue = sum(r["revenue_kzt"] for r in prev) if prev else 0.0
    if prev_revenue > 0 and recent_revenue < prev_revenue * 0.75:
        dip_pct = int((1 - recent_revenue / prev_revenue) * 100)
        rec = await _upsert_recommendation(
            db, org_id,
            rec_type="revenue_dip",
            title=f"Выручка упала на {dip_pct}%",
            body=(
                f"Выручка за последние {days} дней ({int(recent_revenue):,} ₸) "
                f"на {dip_pct}% ниже предыдущего периода ({int(prev_revenue):,} ₸). "
                "Рассмотрите акцию, промо-рассылку или пересмотр меню."
            ),
            confidence_pct=min(80, 50 + dip_pct // 5),
            expected_impact_kzt=int((prev_revenue - recent_revenue) * 0.5) if prev_revenue > recent_revenue else None,
            data={"recent_revenue": round(recent_revenue, 2), "prev_revenue": round(prev_revenue, 2)},
        )
        if rec:
            generated.append(rec)

    # ── Low dialog-to-order conversion ───────────────────────────────────
    total_dialogs = sum(r["dialogs_count"] for r in recent)
    total_confirmed = sum(r["orders_confirmed"] for r in recent)
    if total_dialogs > 20 and total_confirmed < total_dialogs * 0.3:
        conv_pct = int(total_confirmed / total_dialogs * 100) if total_dialogs else 0
        rec = await _upsert_recommendation(
            db, org_id,
            rec_type="low_conversion",
            title=f"Низкая конверсия диалогов: {conv_pct}%",
            body=(
                f"Только {conv_pct}% диалогов завершаются заказом ({total_confirmed} из {total_dialogs}). "
                "Проверьте: бот правильно отвечает на вопросы, меню актуально, "
                "способы оплаты работают."
            ),
            confidence_pct=min(75, 40 + (30 - conv_pct) // 2),
            expected_impact_kzt=None,
            data={"dialogs": total_dialogs, "confirmed": total_confirmed, "conv_pct": conv_pct},
        )
        if rec:
            generated.append(rec)

    return generated


async def generate_recommendations(
    db: AsyncSession,
    org_id: int,
    days: int = 7,
) -> list[BusinessRecommendation]:
    """Генерирует рекомендации на основе данных за последние N дней."""
    # Phase 5 OS: event-driven рекомендации (не читают Order)
    generated: list[BusinessRecommendation] = await _generate_event_driven_recommendations(db, org_id, days)

    # Order-based рекомендации (menu engineering, geo, stoplist)
    orders = await _load_orders(db, org_id, days)
    if len(orders) < 5:
        return generated

    order_generated: list[BusinessRecommendation] = []

    # ── Menu Engineering ──────────────────────────────────────────────────
    eng_rows = menu_engineering_rows(orders)

    # product_boost: высокая конверсия апселла → продвигать
    for row in eng_rows:
        if (row.get("offers", 0) >= _MIN_OFFER_COUNT
                and row.get("conversion_pct", 0) >= _BOOST_CONV_THRESHOLD):
            name = row.get("name") or row.get("item_name") or "Блюдо"
            conv = row["conversion_pct"]
            rev = row.get("upsell_revenue", 0)
            confidence = min(95, 60 + int(row.get("offers", 0) / 2))
            rec = await _upsert_recommendation(
                db, org_id,
                rec_type="product_boost",
                title=f"Продвигать «{name[:60]}» активнее",
                body=(
                    f"Блюдо «{name}» конвертирует допродажу в {conv}% случаев "
                    f"(предложено {row['offers']} раз). "
                    f"Суммарная выручка с допродаж: {int(rev):,} ₸. "
                    "Разместите его в промо-блоке или отдельной категории."
                ),
                confidence_pct=confidence,
                expected_impact_kzt=int(rev * 0.3) if rev else None,
                data={"item_name": name, "conversion_pct": conv, "offers": row["offers"]},
            )
            if rec:
                order_generated.append(rec)
            if len([r for r in order_generated if r.recommendation_type == "product_boost"]) >= 3:
                break

    # pricing_adj: низкая конверсия, много предложений → снизить цену?
    for row in eng_rows:
        if (row.get("offers", 0) >= _MIN_OFFER_COUNT * 2
                and row.get("conversion_pct", 0) < _LOW_CONV_THRESHOLD):
            name = row.get("name") or row.get("item_name") or "Блюдо"
            conv = row["conversion_pct"]
            rec = await _upsert_recommendation(
                db, org_id,
                rec_type="pricing_adj",
                title=f"Пересмотреть цену «{name[:55]}»",
                body=(
                    f"Блюдо «{name}» предлагается гостям {row['offers']} раз, "
                    f"но принимается только в {conv}% случаев. "
                    "Возможно, цена выше ожиданий. Рассмотрите скидку 10-15% или пересмотр позиции в меню."
                ),
                confidence_pct=min(80, 40 + int(row.get("offers", 0) / 3)),
                expected_impact_kzt=None,
                data={"item_name": name, "conversion_pct": conv, "offers": row["offers"]},
            )
            if rec:
                order_generated.append(rec)
            if len([r for r in order_generated if r.recommendation_type == "pricing_adj"]) >= 2:
                break

    # ── Geo Expansion ─────────────────────────────────────────────────────
    geo_rows = delivery_geo_rows(orders)
    for geo in geo_rows:
        if geo.get("orders_per_customer", 0) >= _GEO_LOYALTY_THRESHOLD and geo.get("orders", 0) >= 5:
            addr = geo.get("address_bucket") or geo.get("bucket") or "Район"
            opc = geo["orders_per_customer"]
            rev = geo.get("revenue", 0)
            rec = await _upsert_recommendation(
                db, org_id,
                rec_type="geo_expansion",
                title=f"Развить доставку в «{str(addr)[:50]}»",
                body=(
                    f"Клиенты из этого района заказывают в среднем {opc:.1f} раз "
                    f"(суммарная выручка: {int(rev):,} ₸ за {days} дн.). "
                    "Высокая лояльность — зона для расширения/ускорения доставки."
                ),
                confidence_pct=min(85, 50 + int(geo.get("orders", 0) * 2)),
                expected_impact_kzt=int(rev * 0.2) if rev else None,
                data={"address_bucket": str(addr), "orders": geo.get("orders"), "opc": opc},
            )
            if rec:
                order_generated.append(rec)
            if len([r for r in order_generated if r.recommendation_type == "geo_expansion"]) >= 2:
                break

    # ── Stop-list impact ──────────────────────────────────────────────────
    recent_dt = datetime.now(timezone.utc) - timedelta(days=days)
    from app.services.intelligence import _sql_dt_for_filter
    stoplist_events = (await db.execute(
        select(SystemEvent.payload_json).where(
            SystemEvent.organization_id == org_id,
            SystemEvent.event_type == "stoplist_update",
            SystemEvent.created_at >= _sql_dt_for_filter(recent_dt),
        ).limit(200)
    )).scalars().all()

    stoplist_counts: dict[str, int] = {}
    for payload in stoplist_events:
        if not isinstance(payload, dict):
            continue
        for item_name in (payload.get("items_added_to_stop") or []):
            stoplist_counts[item_name] = stoplist_counts.get(item_name, 0) + 1

    for item_name, count in sorted(stoplist_counts.items(), key=lambda x: -x[1])[:2]:
        if count >= 3:
            rec = await _upsert_recommendation(
                db, org_id,
                rec_type="stoplist_impact",
                title=f"«{item_name[:55]}» часто в стопе",
                body=(
                    f"Позиция «{item_name}» попадала в стоп-лист {count} раз за {days} дней. "
                    "Возможна проблема с поставщиком или хранением. "
                    "Рассмотрите замену поставщика или временное исключение из меню."
                ),
                confidence_pct=min(90, 55 + count * 5),
                expected_impact_kzt=None,
                data={"item_name": item_name, "stoplist_count": count},
            )
            if rec:
                order_generated.append(rec)

    return generated + order_generated


async def generate_autopilot_pricing_recommendation(
    db: AsyncSession,
    org_id: int,
    *,
    days: int = 14,
) -> BusinessRecommendation | None:
    """Генерирует рекомендацию типа autopilot_pricing на основе ценовых сигналов из DailyOrgStats.

    Создаётся автоматически в daily loop; применяется через POST /apply-pricing/{rec_id}.
    Не создаётся при tactic=stable или confidence=low.
    """
    from app.services.analytics_consumer import get_event_stats
    from app.services.owner_dashboard import build_autopilot_pricing

    rows = await get_event_stats(db, org_id, days=days)
    signal = build_autopilot_pricing(rows)
    if signal is None:
        return None
    if signal["tactic"] == "stable" or signal.get("confidence") == "low":
        return None
    adj_pct = signal.get("price_adj_pct", 0)
    if adj_pct == 0:
        return None

    direction = "↑" if adj_pct > 0 else "↓"
    confidence_map = {"high": 85, "medium": 65, "low": 40}
    revenue_impact = None
    cur_rev = signal.get("revenue_ratio", 1.0)
    if adj_pct > 0:
        revenue_impact = int(cur_rev * 1000 * adj_pct / 100)  # грубая оценка +N ₸

    return await _upsert_recommendation(
        db, org_id,
        rec_type="autopilot_pricing",
        title=f"Автопилот: {direction}{abs(adj_pct)}% к ценам меню",
        body=(
            f"{signal['suggestion']} "
            f"(выручка: ×{signal['revenue_ratio']:.2f}, заказы: ×{signal['orders_ratio']:.2f}). "
            "Нажмите «Применить» чтобы автоматически скорректировать цены всех активных позиций."
        ),
        confidence_pct=confidence_map.get(signal.get("confidence") or "medium", 65),
        expected_impact_kzt=revenue_impact,
        data={
            "price_adj_pct": adj_pct,
            "tactic": signal["tactic"],
            "revenue_ratio": signal["revenue_ratio"],
            "orders_ratio": signal["orders_ratio"],
            "current_avg_check": signal.get("current_avg_check"),
            "auto_generated": True,
        },
    )


async def list_recommendations(db: AsyncSession, org_id: int, limit: int = 10) -> list[BusinessRecommendation]:
    rows = (await db.execute(
        select(BusinessRecommendation).where(
            BusinessRecommendation.organization_id == org_id,
            BusinessRecommendation.status.in_(["new", "viewed"]),
        ).order_by(BusinessRecommendation.created_at.desc()).limit(limit)
    )).scalars().all()
    return list(rows)


def _seconds_until_next_4am_utc() -> float:
    """Секунд до следующего UTC 04:00."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def recommendations_daily_loop(session_factory: Any) -> None:
    """Фоновый цикл: генерация рекомендаций раз в сутки в UTC 04:00."""
    while True:
        wait = _seconds_until_next_4am_utc()
        logger.info("Recommendations loop: следующий запуск через %.0f мин.", wait / 60)
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            break

        from app.services.redis_locks import acquire_redis_lock, release_redis_lock

        lock_key = "restomind:bg:recommendations_daily"
        lock_token = await acquire_redis_lock(lock_key, ttl_sec=3600)
        if lock_token is None:
            logger.info("Recommendations loop: замок занят другим инстансом")
            continue

        try:
            from app.db.models import Organization
            async with session_factory() as db:
                orgs = (await db.execute(
                    select(Organization.id).where(Organization.is_active.is_(True))
                )).scalars().all()

            for org_id in orgs:
                try:
                    async with session_factory() as db:
                        recs = await generate_recommendations(db, int(org_id))
                        # Phase 5 OS: autopilot_pricing — генерируется автоматически
                        pricing_rec = await generate_autopilot_pricing_recommendation(db, int(org_id))
                        if pricing_rec:
                            recs = list(recs) + [pricing_rec]
                        await db.commit()
                    if recs:
                        logger.info("Recommendations: org=%s generated=%d", org_id, len(recs))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Recommendations generation failed org=%s: %s", org_id, exc)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Recommendations loop error: %s", exc)
        finally:
            await release_redis_lock(lock_key, lock_token)
