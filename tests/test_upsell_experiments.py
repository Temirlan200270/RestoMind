"""Upsell phrase A/B experiments (RC-C)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.owner_intelligence_upsell import get_upsell_impact
from app.db.models import Organization, UpsellPhraseVariant, UpsellRule
from app.services.upsell_attribution import assign_variant_at_offer
from app.services.upsell_experiments import (
    MIN_SAMPLES_FOR_AUTO_DISABLE,
    OUTCOME_ACCEPTED,
    OUTCOME_REJECTED,
    OUTCOME_SHOWN,
    auto_disable_weak_variants,
    build_experiment_stats,
    load_variants_for_rule,
    pick_weighted_variant,
    record_variant_outcome,
    variant_conversion_rate,
)


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


async def _seed_org_rule(db: AsyncSession) -> tuple[int, int]:
    org = Organization(name="Exp Org", slug="exp-org")
    db.add(org)
    await db.flush()
    rule = UpsellRule(
        organization_id=int(org.id),
        trigger_category="горячее",
        suggest_category="напитки",
        phrase_template="Попробуйте {item_name} за {price} ₸?",
    )
    db.add(rule)
    await db.flush()
    return int(org.id), int(rule.id)


@pytest.mark.asyncio
async def test_load_variants_for_rule_scoped_by_org(db_session: AsyncSession) -> None:
    org_id, rule_id = await _seed_org_rule(db_session)
    other = Organization(name="Other Org", slug="other-org")
    db_session.add(other)
    await db_session.flush()

    db_session.add_all([
        UpsellPhraseVariant(
            organization_id=org_id,
            rule_id=rule_id,
            variant_key="friendly",
            template="A",
            weight=100,
        ),
        UpsellPhraseVariant(
            organization_id=int(other.id),
            rule_id=rule_id,
            variant_key="friendly",
            template="B",
            weight=100,
        ),
    ])
    await db_session.flush()

    rows = await load_variants_for_rule(db_session, org_id, rule_id)
    assert len(rows) == 1
    assert rows[0].template == "A"


def test_pick_weighted_variant_returns_active_template() -> None:
    variants = [
        UpsellPhraseVariant(
            organization_id=1,
            rule_id=1,
            variant_key="a",
            template="Phrase A",
            weight=0,
            is_active=True,
        ),
        UpsellPhraseVariant(
            organization_id=1,
            rule_id=1,
            variant_key="b",
            template="Phrase B",
            weight=100,
            is_active=False,
        ),
    ]
    key, template = pick_weighted_variant(variants)
    assert key == "a"
    assert template == "Phrase A"


def test_pick_weighted_variant_empty_returns_default() -> None:
    key, template = pick_weighted_variant([])
    assert key == "default"
    assert template == ""


@pytest.mark.asyncio
async def test_record_variant_outcome_updates_stats_json(db_session: AsyncSession) -> None:
    org_id, rule_id = await _seed_org_rule(db_session)
    row = UpsellPhraseVariant(
        organization_id=org_id,
        rule_id=rule_id,
        variant_key="direct",
        template="Добавить {item_name}?",
        weight=100,
    )
    db_session.add(row)
    await db_session.flush()

    updated = await record_variant_outcome(
        db_session,
        org_id,
        rule_id,
        "direct",
        OUTCOME_SHOWN,
    )
    assert updated is not None
    assert updated.stats_json["shown"] == 1

    await record_variant_outcome(db_session, org_id, rule_id, "direct", OUTCOME_ACCEPTED)
    await db_session.refresh(row)
    assert row.stats_json["accepted"] == 1
    assert variant_conversion_rate(row.stats_json) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_auto_disable_weak_variants_after_min_samples(db_session: AsyncSession) -> None:
    org_id, rule_id = await _seed_org_rule(db_session)
    strong = UpsellPhraseVariant(
        organization_id=org_id,
        rule_id=rule_id,
        variant_key="strong",
        template="Strong",
        weight=100,
        stats_json={"shown": MIN_SAMPLES_FOR_AUTO_DISABLE, "accepted": 10, "rejected": 0, "ignored": 0},
    )
    weak = UpsellPhraseVariant(
        organization_id=org_id,
        rule_id=rule_id,
        variant_key="weak",
        template="Weak",
        weight=100,
        stats_json={"shown": MIN_SAMPLES_FOR_AUTO_DISABLE, "accepted": 1, "rejected": 0, "ignored": 0},
    )
    db_session.add_all([strong, weak])
    await db_session.flush()

    disabled = await auto_disable_weak_variants(
        db_session,
        org_id,
        rule_id,
        min_samples=MIN_SAMPLES_FOR_AUTO_DISABLE,
    )
    assert int(weak.id) in disabled
    await db_session.refresh(weak)
    assert weak.is_active is False
    assert weak.stats_json.get("auto_disabled") is True

    await db_session.refresh(strong)
    assert strong.is_active is True


@pytest.mark.asyncio
async def test_assign_variant_at_offer_uses_experiment(db_session: AsyncSession) -> None:
    org_id, rule_id = await _seed_org_rule(db_session)
    db_session.add(
        UpsellPhraseVariant(
            organization_id=org_id,
            rule_id=rule_id,
            variant_key="exp_a",
            template="Эксперимент A: {item_name}",
            weight=100,
        ),
    )
    await db_session.flush()

    variant_key, template = await assign_variant_at_offer(
        db_session,
        organization_id=org_id,
        rule_id=rule_id,
        fallback_template="Fallback {item_name}",
    )
    assert variant_key == "exp_a"
    assert "Эксперимент A" in template

    row = await db_session.scalar(
        select(UpsellPhraseVariant).where(
            UpsellPhraseVariant.organization_id == org_id,
            UpsellPhraseVariant.variant_key == "exp_a",
        ).limit(1),
    )
    assert row is not None
    assert row.stats_json["shown"] == 1


@pytest.mark.asyncio
async def test_assign_variant_at_offer_fallback_without_experiments(db_session: AsyncSession) -> None:
    org_id, rule_id = await _seed_org_rule(db_session)
    variant_key, template = await assign_variant_at_offer(
        db_session,
        organization_id=org_id,
        rule_id=rule_id,
        fallback_template="Rule fallback {item_name}",
    )
    assert variant_key == f"rule_{rule_id}"
    assert template == "Rule fallback {item_name}"


@pytest.mark.asyncio
async def test_build_experiment_stats_and_api_response(db_session: AsyncSession) -> None:
    org_id, rule_id = await _seed_org_rule(db_session)
    db_session.add_all([
        UpsellPhraseVariant(
            organization_id=org_id,
            rule_id=rule_id,
            variant_key="v1",
            template="V1",
            weight=100,
            stats_json={"shown": 10, "accepted": 4, "rejected": 3, "ignored": 3},
        ),
        UpsellPhraseVariant(
            organization_id=org_id,
            rule_id=rule_id,
            variant_key="v2",
            template="V2",
            weight=50,
            stats_json={"shown": 8, "accepted": 2, "rejected": 2, "ignored": 4},
        ),
    ])
    await db_session.flush()

    stats = await build_experiment_stats(db_session, org_id)
    assert stats["total_variants"] == 2
    assert stats["active_variants"] == 2
    assert stats["best_variants"]
    assert stats["best_variants"][0]["variant_key"] == "v1"
    assert stats["best_variants"][0]["conversion_rate"] == pytest.approx(40.0)

    req = DummyRequest(org_id)
    payload = await get_upsell_impact(req, db_session, period="today", location_id=None)
    assert "experiment_stats" in payload
    assert payload["experiment_stats"]["total_variants"] == 2
    assert payload["best_variants"]
    assert any(v.get("variant_key") == "v1" or v.get("variant") == "v1" for v in payload["best_variants"])


@pytest.mark.asyncio
async def test_record_variant_outcome_unknown_variant_is_noop(db_session: AsyncSession) -> None:
    org_id, rule_id = await _seed_org_rule(db_session)
    result = await record_variant_outcome(
        db_session,
        org_id,
        rule_id,
        "missing",
        OUTCOME_REJECTED,
    )
    assert result is None
