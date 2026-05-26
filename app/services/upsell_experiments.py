"""Revenue Copilot RC-C — A/B эксперименты с фразами upsell."""

from __future__ import annotations

import random
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UpsellPhraseVariant

OUTCOME_SHOWN = "shown"
OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"
OUTCOME_IGNORED = "ignored"

MIN_SAMPLES_FOR_AUTO_DISABLE = 20
WEAK_VARIANT_RATIO = 0.5
MIN_ACTIVE_VARIANTS = 1

_STAT_KEYS = (OUTCOME_SHOWN, OUTCOME_ACCEPTED, OUTCOME_REJECTED, OUTCOME_IGNORED)


def empty_variant_stats() -> dict[str, int]:
    return {key: 0 for key in _STAT_KEYS}


def normalize_variant_stats(raw: dict[str, Any] | None) -> dict[str, int]:
    base = empty_variant_stats()
    if not isinstance(raw, dict):
        return base
    for key in _STAT_KEYS:
        base[key] = int(raw.get(key) or 0)
    return base


def variant_conversion_rate(stats: dict[str, Any] | None) -> float:
    normalized = normalize_variant_stats(stats if isinstance(stats, dict) else None)
    shown = normalized[OUTCOME_SHOWN]
    if shown <= 0:
        return 0.0
    return normalized[OUTCOME_ACCEPTED] / shown


async def load_variants_for_rule(
    db: AsyncSession,
    org_id: int,
    rule_id: int | None,
    *,
    active_only: bool = True,
) -> list[UpsellPhraseVariant]:
    """Активные варианты фраз для правила в рамках организации."""
    stmt = select(UpsellPhraseVariant).where(
        UpsellPhraseVariant.organization_id == int(org_id),
        UpsellPhraseVariant.rule_id == (int(rule_id) if rule_id is not None else None),
    )
    if active_only:
        stmt = stmt.where(UpsellPhraseVariant.is_active.is_(True))
    stmt = stmt.order_by(UpsellPhraseVariant.id.asc())
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


def pick_weighted_variant(
    variants: list[UpsellPhraseVariant],
) -> tuple[str, str]:
    """Weighted random: variant_key, template."""
    active = [row for row in variants if row.is_active]
    if not active:
        return ("default", "")
    weights = [max(int(row.weight or 0), 1) for row in active]
    chosen = random.choices(active, weights=weights, k=1)[0]
    return (str(chosen.variant_key), str(chosen.template or ""))


async def _get_variant_row(
    db: AsyncSession,
    org_id: int,
    rule_id: int | None,
    variant_key: str,
) -> UpsellPhraseVariant | None:
    key = (variant_key or "").strip()
    if not key:
        return None
    return await db.scalar(
        select(UpsellPhraseVariant).where(
            UpsellPhraseVariant.organization_id == int(org_id),
            UpsellPhraseVariant.rule_id == (int(rule_id) if rule_id is not None else None),
            UpsellPhraseVariant.variant_key == key,
        ).limit(1),
    )


async def record_variant_outcome(
    db: AsyncSession,
    org_id: int,
    rule_id: int | None,
    variant_key: str,
    outcome: str,
    *,
    min_samples: int = MIN_SAMPLES_FOR_AUTO_DISABLE,
) -> UpsellPhraseVariant | None:
    """Инкремент stats_json и авто-отключение слабых вариантов."""
    row = await _get_variant_row(db, org_id, rule_id, variant_key)
    if row is None:
        return None

    outcome_key = (outcome or "").strip().lower()
    if outcome_key not in _STAT_KEYS:
        return None

    stats = normalize_variant_stats(row.stats_json)
    stats[outcome_key] += 1
    row.stats_json = stats
    await db.flush()

    if rule_id is not None:
        await auto_disable_weak_variants(
            db,
            org_id,
            int(rule_id),
            min_samples=min_samples,
        )
    return row


async def auto_disable_weak_variants(
    db: AsyncSession,
    org_id: int,
    rule_id: int,
    *,
    min_samples: int = MIN_SAMPLES_FOR_AUTO_DISABLE,
    weak_ratio: float = WEAK_VARIANT_RATIO,
) -> list[int]:
    """
    Отключает варианты с конверсией заметно ниже лучшего после min_samples показов.
    Возвращает id отключённых строк.
    """
    variants = await load_variants_for_rule(db, org_id, rule_id, active_only=False)
    active = [row for row in variants if row.is_active]
    if len(active) <= MIN_ACTIVE_VARIANTS:
        return []

    eligible = [
        row for row in active
        if normalize_variant_stats(row.stats_json)[OUTCOME_SHOWN] >= int(min_samples)
    ]
    if len(eligible) < 2:
        return []

    rates = {int(row.id): variant_conversion_rate(row.stats_json) for row in eligible}
    best_rate = max(rates.values())
    if best_rate <= 0:
        return []

    threshold = best_rate * float(weak_ratio)
    disabled_ids: list[int] = []
    for row in eligible:
        rate = rates[int(row.id)]
        if rate >= threshold:
            continue
        # Не отключаем последний активный вариант.
        remaining_active = sum(
            1 for candidate in active
            if candidate.is_active and int(candidate.id) != int(row.id)
        )
        if remaining_active < MIN_ACTIVE_VARIANTS:
            continue
        row.is_active = False
        stats = normalize_variant_stats(row.stats_json)
        meta = dict(row.stats_json or {}) if isinstance(row.stats_json, dict) else {}
        meta.update(stats)
        meta["auto_disabled"] = True
        meta["auto_disabled_reason"] = "weak_conversion"
        row.stats_json = meta
        disabled_ids.append(int(row.id))

    if disabled_ids:
        await db.flush()
    return disabled_ids


def _variant_summary(row: UpsellPhraseVariant) -> dict[str, Any]:
    stats = normalize_variant_stats(row.stats_json)
    shown = stats[OUTCOME_SHOWN]
    accepted = stats[OUTCOME_ACCEPTED]
    return {
        "variant_key": row.variant_key,
        "rule_id": row.rule_id,
        "template": row.template,
        "weight": int(row.weight or 0),
        "is_active": bool(row.is_active),
        "shown": shown,
        "accepted": accepted,
        "rejected": stats[OUTCOME_REJECTED],
        "ignored": stats[OUTCOME_IGNORED],
        "conversion_rate": round(variant_conversion_rate(stats) * 100, 1),
    }


async def build_experiment_stats(
    db: AsyncSession,
    org_id: int,
    *,
    rule_id: int | None = None,
) -> dict[str, Any]:
    """Сводка экспериментов для API (best_variants + by_rule)."""
    stmt = select(UpsellPhraseVariant).where(
        UpsellPhraseVariant.organization_id == int(org_id),
    )
    if rule_id is not None:
        stmt = stmt.where(UpsellPhraseVariant.rule_id == int(rule_id))
    stmt = stmt.order_by(UpsellPhraseVariant.rule_id.asc(), UpsellPhraseVariant.id.asc())
    rows = list((await db.execute(stmt)).scalars().all())

    by_rule: dict[int | None, list[dict[str, Any]]] = {}
    for row in rows:
        summaries = by_rule.setdefault(row.rule_id, [])
        summaries.append(_variant_summary(row))

    best_variants: list[dict[str, Any]] = []
    for summary in (_variant_summary(row) for row in rows):
        if summary["shown"] <= 0:
            continue
        best_variants.append(summary)
    best_variants.sort(
        key=lambda item: (-item["conversion_rate"], -item["accepted"], -item["shown"]),
    )

    active_count = sum(1 for row in rows if row.is_active)
    disabled_count = len(rows) - active_count

    return {
        "rules_with_experiments": len(by_rule),
        "total_variants": len(rows),
        "active_variants": active_count,
        "disabled_variants": disabled_count,
        "by_rule": [
            {
                "rule_id": rid,
                "variants": variants,
            }
            for rid, variants in sorted(by_rule.items(), key=lambda pair: (pair[0] is None, pair[0] or 0))
        ],
        "best_variants": best_variants[:10],
    }
