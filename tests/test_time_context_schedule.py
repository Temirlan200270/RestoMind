from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.time_context import check_operational_status, format_org_current_time_block


def test_operational_status_closed_and_next_open() -> None:
    schedule = {
        "mon": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "tue": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "wed": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "thu": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "fri": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "sat": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "sun": {"is_closed": True, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
    }
    now = datetime(2026, 4, 26, 1, 30, tzinfo=ZoneInfo("Asia/Almaty"))  # Sunday
    op = check_operational_status("Asia/Almaty", schedule, now=now)
    assert op.is_business_open is False
    assert op.is_kitchen_open is False
    assert op.next_business_open_at is not None


def test_operational_status_overnight_shift_from_previous_day() -> None:
    schedule = {
        "mon": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "tue": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "wed": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "thu": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "fri": {"is_closed": False, "open": "11:00", "kitchen_close": "01:00", "business_close": "02:00"},
        "sat": {"is_closed": False, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
        "sun": {"is_closed": True, "open": "11:00", "kitchen_close": "22:30", "business_close": "23:00"},
    }
    now = datetime(2026, 4, 25, 1, 30, tzinfo=ZoneInfo("Asia/Almaty"))  # Saturday 01:30, Friday shift still active
    op = check_operational_status("Asia/Almaty", schedule, now=now)
    assert op.is_business_open is True
    assert op.is_kitchen_open is False


def test_format_time_block_contains_operational_status() -> None:
    block = format_org_current_time_block("Asia/Almaty", None)
    assert "OPERATIONAL_STATUS" in block
    assert "TIMEZONE: Asia/Almaty" in block


def test_soft_close_warning_added_for_kitchen_buffer() -> None:
    schedule = {
        "mon": {"is_closed": False, "open": "11:00", "kitchen_close": "23:00", "business_close": "23:30"},
        "tue": {"is_closed": False, "open": "11:00", "kitchen_close": "23:00", "business_close": "23:30"},
        "wed": {"is_closed": False, "open": "11:00", "kitchen_close": "23:00", "business_close": "23:30"},
        "thu": {"is_closed": False, "open": "11:00", "kitchen_close": "23:00", "business_close": "23:30"},
        "fri": {"is_closed": False, "open": "11:00", "kitchen_close": "23:00", "business_close": "23:30"},
        "sat": {"is_closed": False, "open": "11:00", "kitchen_close": "23:00", "business_close": "23:30"},
        "sun": {"is_closed": False, "open": "11:00", "kitchen_close": "23:00", "business_close": "23:30"},
    }
    now = datetime(2026, 4, 27, 22, 45, tzinfo=ZoneInfo("Asia/Almaty"))
    op = check_operational_status("Asia/Almaty", schedule, now=now)
    assert op.is_kitchen_open is True
    assert op.kitchen_closes_in_minutes == 15
    assert "ПРЕДУПРЕДИ КЛИЕНТА" in op.prompt_instruction
