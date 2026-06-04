"""Kitchen Gate v2 — операционный режим смены (нагрузка, доставка, +N минут)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OperationalModeState

KITCHEN_LOAD_NORMAL = "normal"
KITCHEN_LOAD_BUSY = "busy"
KITCHEN_LOAD_OVERLOAD = "overload"

DELIVERY_MODE_NORMAL = "normal"
DELIVERY_MODE_PAUSED = "paused"

DEFAULT_KITCHEN_LOAD = KITCHEN_LOAD_NORMAL
DEFAULT_PREP_TIME_EXTRA_MIN = 0
DEFAULT_DELIVERY_MODE = DELIVERY_MODE_NORMAL
DEFAULT_FORCE_PICKUP_ONLY = False


@dataclass(frozen=True, slots=True)
class OperationalModeSnapshot:
    organization_id: int
    location_id: int | None
    kitchen_load: str
    prep_time_extra_min: int
    delivery_mode: str
    force_pickup_only: bool
    reason: str | None
    expires_at: datetime | None
    updated_by_staff_id: int | None
    updated_at: datetime | None

    @property
    def is_delivery_blocked(self) -> bool:
        return self.delivery_mode == DELIVERY_MODE_PAUSED or self.force_pickup_only

    @property
    def is_pickup_only(self) -> bool:
        return self.force_pickup_only


def default_operational_mode_snapshot(
    organization_id: int,
    *,
    location_id: int | None = None,
) -> OperationalModeSnapshot:
    return OperationalModeSnapshot(
        organization_id=int(organization_id),
        location_id=location_id,
        kitchen_load=DEFAULT_KITCHEN_LOAD,
        prep_time_extra_min=DEFAULT_PREP_TIME_EXTRA_MIN,
        delivery_mode=DEFAULT_DELIVERY_MODE,
        force_pickup_only=DEFAULT_FORCE_PICKUP_ONLY,
        reason=None,
        expires_at=None,
        updated_by_staff_id=None,
        updated_at=None,
    )


def _mode_is_expired(row: OperationalModeState, *, now: datetime | None = None) -> bool:
    exp = row.expires_at
    if exp is None:
        return False
    ref = now or datetime.now(tz=timezone.utc)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= ref


def _row_to_snapshot(row: OperationalModeState) -> OperationalModeSnapshot:
    return OperationalModeSnapshot(
        organization_id=int(row.organization_id),
        location_id=int(row.location_id) if row.location_id is not None else None,
        kitchen_load=str(row.kitchen_load or DEFAULT_KITCHEN_LOAD),
        prep_time_extra_min=int(row.prep_time_extra_min or 0),
        delivery_mode=str(row.delivery_mode or DEFAULT_DELIVERY_MODE),
        force_pickup_only=bool(row.force_pickup_only),
        reason=(str(row.reason).strip() if row.reason else None) or None,
        expires_at=row.expires_at,
        updated_by_staff_id=int(row.updated_by_staff_id) if row.updated_by_staff_id is not None else None,
        updated_at=row.updated_at,
    )


def operational_mode_to_dict(mode: OperationalModeSnapshot) -> dict[str, Any]:
    return {
        "organization_id": mode.organization_id,
        "location_id": mode.location_id,
        "kitchen_load": mode.kitchen_load,
        "prep_time_extra_min": mode.prep_time_extra_min,
        "delivery_mode": mode.delivery_mode,
        "force_pickup_only": mode.force_pickup_only,
        "reason": mode.reason,
        "expires_at": mode.expires_at.isoformat() if mode.expires_at else None,
        "updated_by_staff_id": mode.updated_by_staff_id,
        "updated_at": mode.updated_at.isoformat() if mode.updated_at else None,
        "is_delivery_blocked": mode.is_delivery_blocked,
        "is_pickup_only": mode.is_pickup_only,
    }


async def _fetch_mode_row(
    db: AsyncSession,
    organization_id: int,
    location_id: int | None,
) -> OperationalModeState | None:
    return await db.scalar(
        select(OperationalModeState).where(
            OperationalModeState.organization_id == int(organization_id),
            OperationalModeState.location_id.is_(None)
            if location_id is None
            else OperationalModeState.location_id == int(location_id),
        ),
    )


async def get_operational_mode(
    db: AsyncSession,
    organization_id: int,
    *,
    location_id: int | None = None,
) -> OperationalModeSnapshot:
    """Текущий режим для org+location; при отсутствии — дефолт."""
    oid = int(organization_id)
    if location_id is not None:
        row = await _fetch_mode_row(db, oid, int(location_id))
        if row is not None and not _mode_is_expired(row):
            return _row_to_snapshot(row)

    row = await _fetch_mode_row(db, oid, None)
    if row is not None and not _mode_is_expired(row):
        return _row_to_snapshot(row)

    return default_operational_mode_snapshot(oid, location_id=location_id)


async def set_operational_mode(
    db: AsyncSession,
    organization_id: int,
    *,
    location_id: int | None = None,
    kitchen_load: str | None = None,
    prep_time_extra_min: int | None = None,
    delivery_mode: str | None = None,
    force_pickup_only: bool | None = None,
    reason: str | None = None,
    expires_at: datetime | None = None,
    expires_at_provided: bool = False,
    updated_by_staff_id: int | None = None,
) -> tuple[OperationalModeSnapshot, OperationalModeSnapshot]:
    """Upsert режима; возвращает (before, after)."""
    oid = int(organization_id)
    before = await get_operational_mode(db, oid, location_id=location_id)

    row = await _fetch_mode_row(db, oid, location_id)
    if row is None:
        row = OperationalModeState(
            organization_id=oid,
            location_id=int(location_id) if location_id is not None else None,
        )
        db.add(row)

    if kitchen_load is not None:
        row.kitchen_load = str(kitchen_load).strip()[:16] or DEFAULT_KITCHEN_LOAD
    if prep_time_extra_min is not None:
        row.prep_time_extra_min = max(0, int(prep_time_extra_min))
    if delivery_mode is not None:
        row.delivery_mode = str(delivery_mode).strip()[:16] or DEFAULT_DELIVERY_MODE
    if force_pickup_only is not None:
        row.force_pickup_only = bool(force_pickup_only)
    if reason is not None:
        cleaned = str(reason).strip()
        row.reason = cleaned[:500] if cleaned else None
    if expires_at_provided or expires_at is not None:
        row.expires_at = expires_at
    if updated_by_staff_id is not None:
        row.updated_by_staff_id = int(updated_by_staff_id)

    await db.flush()
    await db.refresh(row)
    after = _row_to_snapshot(row)
    return before, after


def format_operational_mode_for_prompt(mode: OperationalModeSnapshot) -> str:
    """Короткий блок для system prompt LLM."""
    if (
        mode.kitchen_load == DEFAULT_KITCHEN_LOAD
        and mode.prep_time_extra_min == 0
        and mode.delivery_mode == DEFAULT_DELIVERY_MODE
        and not mode.force_pickup_only
    ):
        return ""

    lines = ["OPERATIONAL_MODE:"]
    lines.append(f"KITCHEN_LOAD={mode.kitchen_load}")
    if mode.prep_time_extra_min > 0:
        lines.append(f"PREP_TIME_EXTRA_MIN={mode.prep_time_extra_min}")
    if mode.delivery_mode == DELIVERY_MODE_PAUSED:
        lines.append("DELIVERY_PAUSED=1")
    if mode.force_pickup_only:
        lines.append("PICKUP_ONLY=1")
    if mode.reason:
        lines.append(f"SHIFT_REASON={mode.reason[:200]}")
    if mode.is_delivery_blocked:
        lines.append(
            "INSTRUCTION: Доставка временно недоступна — предлагай только самовывоз или зал."
        )
    elif mode.prep_time_extra_min > 0:
        lines.append(
            f"INSTRUCTION: Сообщи гостю, что время приготовления увеличено на {mode.prep_time_extra_min} мин."
        )
    return "\n".join(lines)


async def fetch_operational_mode_prompt_block(
    organization_id: int,
    *,
    location_id: int | None = None,
) -> str:
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        mode = await get_operational_mode(db, organization_id, location_id=location_id)
    return format_operational_mode_for_prompt(mode)


EXPIRES_PRESET_PLUS_30M = "plus_30m"
EXPIRES_PRESET_PLUS_1H = "plus_1h"
EXPIRES_PRESET_END_OF_SHIFT = "end_of_shift"
EXPIRES_PRESET_RESET = "reset"

VALID_EXPIRES_PRESETS = frozenset({
    EXPIRES_PRESET_PLUS_30M,
    EXPIRES_PRESET_PLUS_1H,
    EXPIRES_PRESET_END_OF_SHIFT,
    EXPIRES_PRESET_RESET,
})


def resolve_expires_preset(
    org_tz: str,
    preset: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Resolve Kitchen Gate expiry preset to UTC datetime (None = clear / reset)."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    key = (preset or "").strip().lower()
    if key == EXPIRES_PRESET_RESET:
        return None
    if key not in VALID_EXPIRES_PRESETS:
        raise ValueError(f"invalid_expires_preset:{key}")

    tz_name = (org_tz or "UTC").strip() or "UTC"
    try:
        zi = ZoneInfo(tz_name)
    except Exception:
        zi = ZoneInfo("UTC")

    ref = (now or datetime.now(tz=timezone.utc)).astimezone(zi)
    if key == EXPIRES_PRESET_PLUS_30M:
        return (ref + timedelta(minutes=30)).astimezone(timezone.utc)
    if key == EXPIRES_PRESET_PLUS_1H:
        return (ref + timedelta(hours=1)).astimezone(timezone.utc)
    # end_of_shift — конец текущего локального дня
    end_local = ref.replace(hour=23, minute=59, second=59, microsecond=0)
    if end_local <= ref:
        end_local = (end_local + timedelta(days=1))
    return end_local.astimezone(timezone.utc)
