"""Пресеты временной паузы ИИ (UTC)."""

from app.services.ai_snooze import snooze_until_for_preset, utc_now


def test_snooze_30m_is_in_future() -> None:
    until = snooze_until_for_preset("30m", "UTC")
    assert until is not None
    assert until > utc_now()


def test_snooze_until_tomorrow_after_now() -> None:
    until = snooze_until_for_preset("until_tomorrow", "UTC")
    assert until is not None
    assert until > utc_now()
