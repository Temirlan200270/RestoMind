"""operational_mode expires_preset resolver."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.operational_mode import (
    EXPIRES_PRESET_PLUS_30M,
    EXPIRES_PRESET_RESET,
    resolve_expires_preset,
)


def test_resolve_expires_preset_plus_30m() -> None:
    now = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    exp = resolve_expires_preset("UTC", EXPIRES_PRESET_PLUS_30M, now=now)
    assert exp is not None
    assert exp == datetime(2026, 6, 2, 12, 30, tzinfo=timezone.utc)


def test_resolve_expires_preset_reset() -> None:
    assert resolve_expires_preset("UTC", EXPIRES_PRESET_RESET) is None


def test_resolve_expires_preset_invalid() -> None:
    with pytest.raises(ValueError, match="invalid_expires_preset"):
        resolve_expires_preset("UTC", "unknown")
