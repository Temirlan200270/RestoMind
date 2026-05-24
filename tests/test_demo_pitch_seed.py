"""Demo seed после pitch: money queue и recovered_kzt для осмотра."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import DailyOrgStats, Organization
from app.services.demo_data import seed_demo_data
from app.services.demo_shift_scene import DEMO_RESCUE_AMOUNT_KZT
from app.services.money_queue import build_money_queue


@pytest.mark.asyncio
async def test_demo_seed_exposes_pitch_explore_risks(db_session) -> None:
    org = Organization(name="Pitch Explore Org", slug="pitch-explore-org")
    db_session.add(org)
    await db_session.flush()

    stats = await seed_demo_data(db_session, organization_id=int(org.id))
    assert stats["skipped"] is False
    assert stats["pitch_risk_logs_added"] >= 3
    assert stats["pitch_risk_bookings_added"] >= 2

    queue = await build_money_queue(db_session, int(org.id))
    summary = queue["summary"]
    kinds = {item["kind"] for item in queue["items"]}

    assert summary["slow_chats"] >= 2
    assert summary["abandoned_drafts"] >= 2
    assert "slow_chat" in kinds
    assert "abandoned_draft" in kinds
    assert "booking_at_risk" in kinds

    draft_amounts = [
        float(item["amount_kzt"])
        for item in queue["items"]
        if item["kind"] == "abandoned_draft"
    ]
    assert any(abs(amount - DEMO_RESCUE_AMOUNT_KZT) < 50 for amount in draft_amounts)

    stats_row = await db_session.scalar(
        select(DailyOrgStats).where(
            DailyOrgStats.organization_id == int(org.id),
            DailyOrgStats.day == date.today(),
        ),
    )
    assert stats_row is not None
    assert float(stats_row.recovered_kzt) == float(DEMO_RESCUE_AMOUNT_KZT)


def test_unified_demo_login_still_single_entry() -> None:
    """Объединение: одна кнопка demo-login → pitch, без второго входа."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    login = (repo / "app" / "templates" / "screens" / "_login.html").read_text(encoding="utf-8")
    js = (repo / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")

    assert login.count("submitDemoLogin()") == 1
    assert "submitDemoLoginWithScene" not in login
    assert "submitDemoLoginWithScene" not in js

    demo_block = js.split("async submitDemoLogin()")[1].split("async loadOrgProfile()")[0]
    assert "DEMO_SHIFT_SCENE_DEFAULT" in demo_block
    assert "startDemoShiftScene" in demo_block
    assert "_pendingDemoSceneId" in demo_block
