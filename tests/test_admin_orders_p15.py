"""P1.5 regressions for admin orders payload."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.passwords import hash_password
from app.db.models import ChatLog, Order, Organization, StaffRole, StaffUser, User


@pytest.mark.asyncio
async def test_admin_orders_returns_failed_whatsapp_count(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    async with sf() as db:
        org = Organization(name="P15 Orders", slug="p15-orders")
        db.add(org)
        await db.flush()
        user = User(organization_id=int(org.id), phone="+77001112233", name="Aigerim")
        db.add(user)
        await db.flush()
        order = Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status="sent_to_iiko",
            items_json={"items": [{"name": "Плов", "quantity": 1}]},
            total_price=3900,
            created_at=created_at,
        )
        db.add(order)
        db.add(
            ChatLog(
                organization_id=int(org.id),
                user_id=int(user.id),
                role="operator",
                content="Ваш заказ уже на кухне",
                delivery_status="failed",
                created_at=created_at + timedelta(minutes=10),
            ),
        )
        db.add(
            ChatLog(
                organization_id=int(org.id),
                user_id=int(user.id),
                role="user",
                content="Спасибо",
                delivery_status="failed",
                created_at=created_at + timedelta(minutes=11),
            ),
        )
        db.add(
            StaffUser(
                organization_id=int(org.id),
                tenant_owner_id=None,
                email="p15-orders@test.kz",
                password_hash=hash_password("secret123"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()

    login = await ac.post(
        "/api/admin/auth/login",
        json={"email": "p15-orders@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    response = await ac.get("/api/admin/orders", params={"q": "+77001112233", "size": 10, "page": 1})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    row = payload["items"][0]
    assert row["status"] == "sent_to_iiko"
    assert row["failed_whatsapp_near_order"] == 1
