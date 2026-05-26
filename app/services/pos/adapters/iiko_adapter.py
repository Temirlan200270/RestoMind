"""iiko POS adapter — wraps menu_sync and org_iiko credentials."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.menu_sync import sync_menu_from_iiko, sync_stop_lists
from app.services.org_iiko import resolve_org_iiko_credentials


class IikoPOSAdapter:
    provider_slug = "iiko"

    async def health(self, db: AsyncSession, organization_id: int) -> dict[str, Any]:
        creds = await resolve_org_iiko_credentials(db, organization_id)
        configured = creds is not None
        return {
            "ok": configured,
            "provider": self.provider_slug,
            "configured": configured,
        }

    async def send_order(
        self,
        db: AsyncSession,
        organization_id: int,
        order_id: int,
    ) -> dict[str, Any]:
        """Order dispatch stays on the legacy iiko webhook path for now."""
        _ = db, organization_id, order_id
        return {
            "ok": False,
            "provider": self.provider_slug,
            "error": "use legacy iiko order dispatch path",
        }

    async def sync_menu(self, db: AsyncSession, organization_id: int) -> dict[str, Any]:
        creds = await resolve_org_iiko_credentials(db, organization_id)
        if creds is None:
            raise ValueError("iiko credentials not configured")
        return await sync_menu_from_iiko(
            db,
            creds.api_login,
            creds.iiko_organization_id,
            restomind_organization_id=organization_id,
        )

    async def sync_stoplist(self, db: AsyncSession, organization_id: int) -> dict[str, Any]:
        creds = await resolve_org_iiko_credentials(db, organization_id)
        if creds is None:
            raise ValueError("iiko credentials not configured")
        return await sync_stop_lists(
            db,
            creds.api_login,
            creds.iiko_organization_id,
            terminal_group_id=creds.terminal_group_id or None,
            menu_organization_id=organization_id,
        )
