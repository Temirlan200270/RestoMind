from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Order, OrderStatus, Organization, User
from app.services.shift_control import _saved_today_kzt


@pytest.mark.asyncio
async def test_saved_today_kzt_counts_completed_orders(db_session) -> None:
    org = Organization(name="Shift Metrics Org", slug="shift-metrics-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005556002", name="Metrics Guest")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.COMPLETED.value,
            total_price=9000,
            items_json={"items": [{"name": "Сет", "quantity": 1, "item_total": 9000}]},
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=5000,
            items_json={"items": [{"name": "Черновик", "quantity": 1, "item_total": 5000}]},
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
    )
    await db_session.flush()

    saved = await _saved_today_kzt(db_session, int(org.id), location_id=None, allowed_location_ids=None)

    assert saved == 9000.0
