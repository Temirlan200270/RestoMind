"""Factory for read-only iiko sales clients (Cloud OLAP or Server OLAP v2)."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization
from app.integrations.iiko_client import IikoClient
from app.integrations.iiko_server_client import IikoServerClient
from app.services.org_iiko import (
    OrgIikoCredentials,
    resolve_org_iiko_credentials,
    resolve_org_iiko_server_credentials,
)


class IikoSalesClient(Protocol):
    async def __aenter__(self) -> "IikoSalesClient": ...

    async def __aexit__(self, *args: Any) -> None: ...

    async def fetch_olap_sales(
        self,
        organization_id: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]: ...

    async def fetch_product_expenses(
        self,
        organization_id: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]: ...


def org_sales_data_source(org: Organization | None) -> str:
    raw = (getattr(org, "iiko_data_source", "") or settings.iiko_data_source or "cloud").strip().lower()
    return "server" if raw == "server" else "cloud"


async def resolve_iiko_sales_client(
    db: AsyncSession,
    organization_id: int,
) -> tuple[IikoSalesClient, OrgIikoCredentials, str] | None:
    """Return client + Cloud org credentials + selected data source."""
    org = await db.get(Organization, organization_id)
    cloud_creds = await resolve_org_iiko_credentials(db, organization_id)
    if cloud_creds is None:
        return None

    source = org_sales_data_source(org)
    if source == "server":
        server_creds = await resolve_org_iiko_server_credentials(db, organization_id)
        if server_creds is not None:
            return (
                IikoServerClient(
                    host=server_creds.host,
                    port=server_creds.port,
                    login=server_creds.login,
                    password=server_creds.password,
                    department_id=server_creds.department_id,
                ),
                cloud_creds,
                "server",
            )

    return IikoClient(api_login=cloud_creds.api_login), cloud_creds, "cloud"
