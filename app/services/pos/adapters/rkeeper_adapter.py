"""r_keeper POS adapter — stub client + menu/stop-list sync into ``menu_items``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem
from app.integrations.rkeeper_client import RKeeperClient
from app.services.org_rkeeper import resolve_org_rkeeper_credentials


class RKeeperPOSAdapter:
    provider_slug = "rkeeper"

    async def _client_for_org(self, db: AsyncSession, organization_id: int) -> RKeeperClient:
        creds = await resolve_org_rkeeper_credentials(db, organization_id)
        if creds is None:
            raise ValueError("r_keeper credentials not configured")
        return RKeeperClient(server_url=creds.server_url, object_id=creds.object_id)

    async def health(self, db: AsyncSession, organization_id: int) -> dict[str, Any]:
        creds = await resolve_org_rkeeper_credentials(db, organization_id)
        if creds is None:
            return {"ok": False, "provider": self.provider_slug, "configured": False}
        client = RKeeperClient(server_url=creds.server_url, object_id=creds.object_id)
        out = await client.health()
        out["configured"] = True
        return out

    async def sync_menu(self, db: AsyncSession, organization_id: int) -> dict[str, Any]:
        client = await self._client_for_org(db, organization_id)
        payload = await client.fetch_menu()
        items = [row for row in (payload.get("items") or []) if isinstance(row, dict)]

        q = select(MenuItem).where(
            or_(
                MenuItem.organization_id == int(organization_id),
                MenuItem.organization_id.is_(None),
            )
        )
        existing_result = await db.execute(q)
        existing_by_ext: dict[str, MenuItem] = {}
        for mi in existing_result.scalars().all():
            if mi.iiko_id:
                existing_by_ext[str(mi.iiko_id).strip()] = mi

        created = 0
        updated = 0
        for row in items:
            ext_id = str(row.get("id") or "").strip()
            name = str(row.get("name") or "").strip()
            if not ext_id or not name:
                continue
            category = str(row.get("category") or "").strip() or "Без категории"
            price = float(row.get("price") or 0.0)
            description = str(row.get("description") or "")

            item = existing_by_ext.get(ext_id)
            if item is None:
                item = MenuItem(
                    organization_id=int(organization_id),
                    iiko_id=ext_id,
                    name=name,
                    category=category,
                    price=price,
                    description=description,
                    is_available=True,
                )
                db.add(item)
                existing_by_ext[ext_id] = item
                created += 1
            else:
                item.name = name
                item.category = category
                item.price = price
                item.description = description
                if item.organization_id is None:
                    item.organization_id = int(organization_id)
                updated += 1

        await db.flush()
        return {
            "created": created,
            "updated": updated,
            "total": len(items),
            "provider": self.provider_slug,
        }

    async def sync_stoplist(self, db: AsyncSession, organization_id: int) -> dict[str, Any]:
        client = await self._client_for_org(db, organization_id)
        payload = await client.fetch_stoplist()
        stopped_ids = {
            str(x).strip()
            for x in (payload.get("stopped_ids") or [])
            if str(x).strip()
        }

        q = select(MenuItem).where(MenuItem.organization_id == int(organization_id))
        rows = (await db.execute(q)).scalars().all()
        stopped_count = 0
        restored_count = 0
        for item in rows:
            ext_id = str(item.iiko_id or "").strip()
            if not ext_id:
                continue
            should_stop = ext_id in stopped_ids
            if should_stop and item.is_available:
                item.is_available = False
                stopped_count += 1
            elif not should_stop and not item.is_available:
                item.is_available = True
                restored_count += 1

        await db.flush()
        return {
            "stopped": stopped_count,
            "restored": restored_count,
            "provider": self.provider_slug,
        }

    async def send_order(
        self,
        db: AsyncSession,
        organization_id: int,
        order_id: int,
    ) -> dict[str, Any]:
        """Stub — отправка заказа в r_keeper будет реализована позже."""
        _ = db, organization_id, order_id
        return {
            "ok": False,
            "provider": self.provider_slug,
            "error": "send_order not implemented for r_keeper yet",
        }
