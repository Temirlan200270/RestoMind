"""Тесты расписания казанов для плова на стопе."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.models import MenuItem
from app.services.plov_kazan_schedule import (
    compute_next_kazan_batch,
    enrich_plov_kazan_reply_if_needed,
    format_plov_kazan_schedule_prompt_block,
    is_plov_menu_item,
    plov_on_stop,
    resolve_plov_kazan_batch_times,
    stopped_plov_items,
)
from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_brain import _FALLBACK_RESPONSE


def _item(name: str, *, available: bool = True, portion_kind: str = "single", category: str = "Горячее") -> MenuItem:
    return MenuItem(
        name=name,
        category=category,
        price=2790.0,
        is_available=available,
        portion_kind=portion_kind,
        organization_id=1,
    )


def test_next_kazan_batch_morning_before_first_slot() -> None:
    tz = ZoneInfo("Etc/GMT-5")
    now = datetime(2026, 5, 26, 8, 6, tzinfo=tz)
    batch = compute_next_kazan_batch(
        timezone_name="Etc/GMT-5",
        batch_times=("12:00", "16:00", "19:00"),
        now=now,
    )
    assert batch is not None
    assert batch.next_batch_hm == "12:00"
    assert batch.is_today is True
    assert batch.wait_minutes == 234


def test_next_kazan_batch_after_last_slot_goes_tomorrow() -> None:
    tz = ZoneInfo("Etc/GMT-5")
    now = datetime(2026, 5, 26, 20, 0, tzinfo=tz)
    batch = compute_next_kazan_batch(
        timezone_name="Etc/GMT-5",
        batch_times=("12:00", "16:00", "19:00"),
        now=now,
    )
    assert batch is not None
    assert batch.next_batch_hm == "12:00"
    assert batch.is_today is False


def test_any_plov_menu_item_including_company_set() -> None:
    assert is_plov_menu_item(_item("Плов Праздничный баранина"))
    assert is_plov_menu_item(
        _item("ПловХана сет: 10-12 персон", portion_kind="shareable", category="Блюда на компанию"),
    )
    assert not is_plov_menu_item(_item("Лагман", category="Горячее"))


def test_stopped_plov_detection_includes_company_set() -> None:
    menu = [
        _item("Плов Праздничный баранина", available=True),
        _item("ПловХана сет: 10-12 персон", available=False, portion_kind="shareable", category="Блюда на компанию"),
    ]
    assert stopped_plov_items(menu) == ["ПловХана сет: 10-12 персон"]
    assert plov_on_stop(menu) is True


def test_stopped_portion_plov_detection() -> None:
    menu = [
        _item("Плов Праздничный баранина", available=False),
        _item("ПловХана сет: 10-12 персон", available=True, portion_kind="shareable", category="Блюда на компанию"),
    ]
    assert stopped_plov_items(menu) == ["Плов Праздничный баранина"]
    assert plov_on_stop(menu) is True


def test_prompt_block_contains_next_slot_and_instruction() -> None:
    tz = ZoneInfo("Etc/GMT-5")
    now = datetime(2026, 5, 26, 8, 6, tzinfo=tz)
    menu = [_item("Плов Праздничный баранина", available=False)]
    block = format_plov_kazan_schedule_prompt_block(
        menu,
        timezone_name="Etc/GMT-5",
        now=now,
    )
    assert "12:00" in block
    assert "16:00" in block
    assert "19:00" in block
    assert "Устроит" in block or "устроит" in block.lower()
    assert "на стопе" in block


def test_prompt_block_for_stopped_company_set() -> None:
    tz = ZoneInfo("Etc/GMT-5")
    now = datetime(2026, 5, 26, 8, 6, tzinfo=tz)
    menu = [
        _item(
            "ПловХана сет: 10-12 персон",
            available=False,
            portion_kind="shareable",
            category="Блюда на компанию",
        ),
    ]
    block = format_plov_kazan_schedule_prompt_block(
        menu,
        timezone_name="Etc/GMT-5",
        now=now,
    )
    assert "ПловХана сет" in block
    assert "12:00" in block
    assert "на стопе" in block


def test_resolve_batch_times_from_org_meta() -> None:
    times = resolve_plov_kazan_batch_times({"plov_kazan_batch_times": ["11:30", "15:00"]})
    assert times == ("11:30", "15:00")


def test_plov_enrichment_skips_technical_fallback() -> None:
    response = enrich_plov_kazan_reply_if_needed(
        AIBrainResponse(intent="escalate", reply_text=_FALLBACK_RESPONSE.reply_text),
        "Остальные виды плова есть?",
        [_item("Фитнес плов", available=False)],
        timezone_name="Etc/GMT-5",
        now=datetime(2026, 5, 26, 18, 38, tzinfo=ZoneInfo("Etc/GMT-5")),
    )

    assert response.reply_text == _FALLBACK_RESPONSE.reply_text
