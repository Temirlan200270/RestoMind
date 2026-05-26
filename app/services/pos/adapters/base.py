"""POS adapter protocol and registry (Wave 4 Phase 1)."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


VALID_POS_PROVIDERS: frozenset[str] = frozenset({"iiko", "rkeeper"})


class POSAdapter(Protocol):
    """Unified POS integration surface for menu / stop-list sync."""

    provider_slug: str

    async def sync_menu(self, db: AsyncSession, organization_id: int) -> dict[str, Any]:
        """Pull menu into ``menu_items`` for the organization."""
        ...

    async def sync_stoplist(self, db: AsyncSession, organization_id: int) -> dict[str, Any]:
        """Pull stop-list state for the organization."""
        ...

    async def health(self, db: AsyncSession, organization_id: int) -> dict[str, Any]:
        """POS connectivity / credential readiness."""
        ...

    async def send_order(
        self,
        db: AsyncSession,
        organization_id: int,
        order_id: int,
    ) -> dict[str, Any]:
        """Push confirmed order to POS (provider-specific)."""
        ...


ADAPTER_REGISTRY: dict[str, type[POSAdapter]] = {}


def get_pos_adapter_class(provider_slug: str) -> type[POSAdapter] | None:
    key = (provider_slug or "").strip().lower()
    return ADAPTER_REGISTRY.get(key)


def register_pos_adapter(name: str, cls: type[POSAdapter]) -> None:
    ADAPTER_REGISTRY[(name or "").strip().lower()] = cls


async def get_pos_adapter(db: AsyncSession, organization_id: int) -> POSAdapter:
    """Resolve adapter instance for organization (defaults to iiko)."""
    from app.db.models import Organization

    org = await db.get(Organization, int(organization_id))
    slug = (getattr(org, "pos_provider", None) or "iiko").strip().lower()
    cls = get_pos_adapter_class(slug)
    if cls is None:
        raise ValueError(f"POS adapter not registered: {slug}")
    return cls()
