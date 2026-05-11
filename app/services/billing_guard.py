"""
Проверки блокировки tenant по биллингу (E2.3).

Контракт админ-API относительно биллинга держим следующим:

* `Tenant.plan_status == "suspended"` — единственный сегодня источник
  блокировки (auto-suspend по лимитам не реализован, см. ROADMAP).
* На входе (`POST /api/admin/auth/login`, `POST /api/admin/auth/demo-login`,
  `POST /api/admin/auth/select-org`) и на любом защищённом запросе
  (`require_admin_session_active`) при suspended → **HTTP 403** с
  `detail` = `BILLING_SUSPENDED_DETAIL` и заголовком
  `X-RestoMind-Suspended-Reason: tenant_suspended`. Заголовок —
  машиночитаемый сигнал для UI/мониторинга, не ломает старых клиентов.
* На успешном `GET /api/admin/auth/me` (контракт: см. PARALLEL_AI_PLAN §4)
  всегда возвращается стабильное поле ``billing_blocked: false``.
  Поле остаётся в payload даже если в будущем появится мягкая блокировка
  (например, тёплое окно после превышения лимита) — UI сможет включить
  ветку без новой версии API.
* Super-admin сессии не блокируются биллингом ни на одном эндпоинте
  (платформенный доступ важнее статуса арендатора).
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, Tenant

BILLING_SUSPENDED_DETAIL = "Доступ приостановлен по биллингу. Свяжитесь с поддержкой."
BILLING_SUSPENDED_HEADER = "X-RestoMind-Suspended-Reason"
BILLING_SUSPENDED_HEADER_VALUE = "tenant_suspended"


def billing_suspended_response_headers() -> dict[str, str]:
    """Заголовки для 403-ответа при биллинговой блокировке (UI читает в `fetch`)."""
    return {BILLING_SUSPENDED_HEADER: BILLING_SUSPENDED_HEADER_VALUE}


def billing_suspended_http_exception() -> HTTPException:
    """
    Стандартный 403 при `tenant.plan_status == "suspended"`.

    Возвращаемое исключение нужно пробросить через ``raise`` в вызывающей
    функции — так трассировка указывает на реальную точку отказа, а не на
    помощник.
    """
    return HTTPException(
        status_code=403,
        detail=BILLING_SUSPENDED_DETAIL,
        headers=billing_suspended_response_headers(),
    )


def tenant_is_billing_suspended(tenant: Tenant | None) -> bool:
    if tenant is None:
        return False
    return (tenant.plan_status or "").strip().lower() == "suspended"


async def load_tenant_for_organization(db: AsyncSession, organization_id: int) -> Tenant | None:
    org = await db.get(Organization, int(organization_id))
    if org is None or org.tenant_id is None:
        return None
    return await db.get(Tenant, int(org.tenant_id))


async def tenant_billing_blocks_inbound(db: AsyncSession, org: Organization) -> bool:
    """Блок входящих каналов (WhatsApp) для филиала при suspended у tenant."""
    if org.tenant_id is None:
        return False
    tenant = await db.get(Tenant, int(org.tenant_id))
    return tenant_is_billing_suspended(tenant)


__all__ = (
    "BILLING_SUSPENDED_DETAIL",
    "BILLING_SUSPENDED_HEADER",
    "BILLING_SUSPENDED_HEADER_VALUE",
    "billing_suspended_response_headers",
    "billing_suspended_http_exception",
    "tenant_is_billing_suspended",
    "load_tenant_for_organization",
    "tenant_billing_blocks_inbound",
)
