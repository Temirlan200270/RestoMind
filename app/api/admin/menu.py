"""Menu and integrations admin API (E0.1 split from _monolith.py)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import MenuItem
from app.db.session import get_db
from app.services.integration_health import (
    build_status_payload,
    list_integration_events,
    record_menu_sync,
    record_stoplist_sync,
)
from app.services.iiko_onboarding import setup_organization_iiko, verify_iiko_api_login
from app.services.iiko_sync_tasks import run_full_iiko_sync_for_org
from app.services.menu_embeddings import reindex_organization_menu_embeddings
from app.services.menu_cost_import import import_menu_costs_from_csv, preview_menu_costs_from_csv
from app.services.menu_sync import sync_stop_lists
from app.services.integration_config import iiko_effective_configured, whatsapp_effective_configured
from app.services.order_logic import invalidate_menu_context_cache
from .cache_utils import json_with_etag, weak_etag_from_parts
from .deps import (
    _iiko_login_org_for_tenant,
    _menu_item_in_org,
    _menu_tenant_clause,
    admin_org_from_session,
    require_admin_session_active,
    require_superadmin,
)
from .menu_schemas import (
    ClearMenuBody,
    MenuItemCreateBody,
    MenuItemPatchBody,
    menu_item_dict as _menu_item_dict,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["Menu & Integrations"],
    dependencies=[Depends(require_admin_session_active)],
)


# ─── Schemas ──────────────────────────────────────────────


class IikoVerifyBody(BaseModel):
    api_login: str = Field(..., min_length=2, description="Ключ apiLogin из iiko Cloud")


class IikoSetupBody(BaseModel):
    api_login: str = Field(..., min_length=2)
    iiko_organization_id: str = Field(..., min_length=8)
    terminal_group_id: str = ""


# ─── Search ───────────────────────────────────────────────


@router.get("/search")
async def global_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Поиск по телефону/имени: заказы, чаты, бронирования (для Ctrl+K)."""
    from app.db.models import Booking, ChatLog, Order, User
    from app.services.intelligence_analytics import order_meta_from_items_json
    from app.services.tenant_scope import orders_tenant_clause as _orders_tenant_clause

    org_id = admin_org_from_session(request)
    raw = q.strip()
    term = f"%{raw}%"
    out_orders: list[dict] = []
    o_clauses = [
        User.phone.ilike(term),
        func.coalesce(User.name, "").ilike(term),
    ]
    try:
        oid = int(raw)
        if 0 < oid < 2**31:
            o_clauses.append(Order.id == oid)
    except ValueError:
        pass
    oq = (
        select(Order, User.phone, User.name)
        .join(User, Order.user_id == User.id)
        .where(_orders_tenant_clause(org_id), User.organization_id == org_id, or_(*o_clauses))
        .order_by(Order.created_at.desc())
        .limit(limit)
    )

    r1 = await db.execute(oq)
    for o, p, nm in r1.all():
        meta = order_meta_from_items_json(o.items_json if isinstance(o.items_json, dict) else None)
        out_orders.append(
            {
                "id": o.id,
                "status": o.status,
                "user_phone": p,
                "user_name": nm,
                "total_price": float(o.total_price),
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "order_type": meta.get("order_type"),
                "payment_method": meta.get("payment_method"),
            },
        )

    chats_out: list[dict] = []
    cq = (
        select(User.phone, User.name, func.max(ChatLog.created_at))
        .join(ChatLog, ChatLog.user_id == User.id)
        .where(
            User.organization_id == org_id,
            or_(User.phone.ilike(term), func.coalesce(User.name, "").ilike(term)),
        )
        .group_by(User.id, User.phone, User.name)
        .order_by(func.max(ChatLog.created_at).desc())
        .limit(limit)
    )
    r2 = await db.execute(cq)
    for p, nm, last_at in r2.all():
        chats_out.append(
            {
                "phone": p,
                "user_name": nm,
                "last_at": last_at.isoformat() if last_at else None,
            },
        )

    book_out: list[dict] = []
    bq = (
        select(Booking, User.phone, User.name)
        .join(User, Booking.user_id == User.id)
        .where(User.organization_id == org_id, User.phone.ilike(term))
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )
    r3 = await db.execute(bq)
    for b, p, nm in r3.all():
        book_out.append(
            {
                "id": b.id,
                "user_phone": p,
                "user_name": nm,
                "date": b.booking_date.isoformat(),
                "time": b.booking_time.isoformat(),
                "hall": b.hall,
                "status": b.status,
            },
        )

    return {"q": raw, "orders": out_orders, "chats": chats_out, "bookings": book_out}


# ─── Integrations status ──────────────────────────────────


@router.get("/integrations/status")
async def integrations_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Состояние интеграций для админки: индикаторы iiko / WhatsApp."""
    from app.db.models import Organization, OrganizationIntegrationSync

    org_id = admin_org_from_session(request)
    iiko_ok = await iiko_effective_configured(db, org_id)
    wa_ok = await whatsapp_effective_configured(db, org_id)
    base = await build_status_payload(
        db,
        organization_id=int(org_id),
        iiko_configured=iiko_ok,
        whatsapp_configured=wa_ok,
    )
    pub = (settings.public_base_url or "").strip().rstrip("/")
    webhook_url = f"{pub}/api/whatsapp/webhook" if pub else None
    base["webhook_url"] = webhook_url
    base["whatsapp_verify_token_hint"] = (
        settings.whatsapp_verify_token[:4] + "…"
        if settings.whatsapp_verify_token
        else None
    )
    base["openai_configured"] = bool(str(settings.openai_api_key or "").strip())
    base["whatsapp_voice_replies_enabled"] = bool(settings.whatsapp_voice_replies)
    base["iiko_secrets_encrypt_ready"] = bool((settings.app_secrets_fernet_key or "").strip())
    org_row = await db.get(Organization, org_id)
    base["prepayment_enforced"] = bool(getattr(org_row, "prepayment_enforced", True)) if org_row else True
    base["auto_send_to_iiko_after_payment"] = bool(
        getattr(org_row, "auto_send_to_iiko_after_payment", False),
    ) if org_row is not None else False
    from app.services.pos.adapters.base import VALID_POS_PROVIDERS

    base["pos_provider"] = (
        (getattr(org_row, "pos_provider", None) or "iiko").strip().lower()
        if org_row is not None
        else "iiko"
    )
    base["pos_providers_available"] = sorted(VALID_POS_PROVIDERS)

    # Payment providers: per-org config + env-var availability
    _pcj: dict = getattr(org_row, "payment_config_json", None) if org_row else None  # type: ignore[assignment]
    _pcj = _pcj if isinstance(_pcj, dict) else {}
    base["payment_providers"] = {
        slug: {
            "enabled": bool((_pcj.get(slug) or {}).get("enabled", False)),
            "secret_configured": env_ok,
        }
        for slug, env_ok in (
            ("freedom_pay", bool((settings.freedom_pay_webhook_secret or "").strip())),
            ("kaspi", bool((settings.kaspi_webhook_hmac_secret or "").strip())),
            ("cloudpayments", bool((settings.cloudpayments_api_secret or "").strip())),
        )
    }
    tg_token_ok = bool(str(settings.telegram_bot_token or "").strip())
    tg_global_chat_ok = bool(str(settings.telegram_admin_chat_id or "").strip())
    tg_org_chat_ok = bool(
        str(getattr(org_row, "telegram_ops_chat_id", "") or "").strip(),
    ) if org_row is not None else False
    base["telegram_configured"] = tg_token_ok and (tg_global_chat_ok or tg_org_chat_ok)

    sync_row = await db.get(OrganizationIntegrationSync, int(org_id))
    etag = weak_etag_from_parts(
        org_id,
        iiko_ok,
        wa_ok,
        base.get("last_stoplist"),
        base.get("last_menu_sync"),
        base.get("last_inventory_sync"),
        base.get("prepayment_enforced"),
        base.get("auto_send_to_iiko_after_payment"),
        base.get("payment_providers"),
        base.get("telegram_configured"),
        getattr(sync_row, "last_stoplist_at", None),
        getattr(sync_row, "last_menu_sync_at", None),
        getattr(sync_row, "last_inventory_sync_at", None),
    )
    return json_with_etag(request, base, etag)


# ─── Integration events ───────────────────────────────────


@router.get("/integrations/events")
async def integrations_events(
    request: Request,
    limit: int = Query(40, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Журнал последних событий синхронизации (меню, стоп-листы)."""
    events = await list_integration_events(
        db, limit=limit, organization_id=admin_org_from_session(request),
    )
    return {"events": events}


# ─── iiko verify / setup ─────────────────────────────────


@router.post("/integrations/iiko/verify")
async def integrations_iiko_verify(body: IikoVerifyBody) -> dict:
    """Проверить ключ и получить список организаций iiko (ключ не сохраняется)."""
    try:
        orgs = await verify_iiko_api_login(body.api_login)
    except Exception as exc:
        logger.warning("iiko verify failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    return {"ok": True, "organizations": orgs}


@router.post("/integrations/iiko/setup")
async def integrations_iiko_setup(
    request: Request,
    body: IikoSetupBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Сохранить ключ (зашифрованно при APP_SECRETS_FERNET_KEY) и импортировать меню филиала."""
    from app.db.models import Organization

    org_id = admin_org_from_session(request)
    enc = bool((settings.app_secrets_fernet_key or "").strip())
    try:
        stats = await setup_organization_iiko(
            db,
            organization_id=org_id,
            api_login_plain=body.api_login.strip(),
            iiko_organization_uuid=body.iiko_organization_id.strip(),
            encrypt_login=enc,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("iiko setup: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    org = await db.get(Organization, org_id)
    if org is not None and (body.terminal_group_id or "").strip():
        org.iiko_terminal_group_id = body.terminal_group_id.strip()
    sk = stats.get("skipped")
    detail_m = (
        f"Синхронизация меню: успешно "
        f"(всего {stats.get('total', 0)}, новых {stats.get('created', 0)}, обновлено {stats.get('updated', 0)}"
        + (f", пропущено {sk}" if sk else "")
        + ")"
    )
    await record_menu_sync(db, True, None, detail=detail_m, organization_id=org_id)
    await db.commit()
    return {"ok": True, "stats": stats, "encrypted": enc}


# ─── Setup status ─────────────────────────────────────────


@router.get("/setup-status")
async def setup_status(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Прогресс онбординга (Stripe-style) для текущего филиала."""
    from app.db.models import KnowledgeItem, Organization, PackagingRule, UpsellRule

    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    iiko_ok = await iiko_effective_configured(db, org_id)
    menu_n = int(
        await db.scalar(
            select(func.count()).select_from(MenuItem).where(MenuItem.organization_id == org_id),
        )
        or 0,
    )
    packaging_n = int(
        await db.scalar(
            select(func.count()).select_from(PackagingRule).where(
                PackagingRule.organization_id == org_id,
                PackagingRule.is_active.is_(True),
            ),
        )
        or 0,
    )
    wa_ok = await whatsapp_effective_configured(db, org_id)
    rules_n = int(
        await db.scalar(
            select(func.count()).select_from(UpsellRule).where(
                UpsellRule.organization_id == org_id,
                UpsellRule.is_active.is_(True),
            ),
        )
        or 0,
    )
    kb_n = int(
        await db.scalar(
            select(func.count()).select_from(KnowledgeItem).where(
                KnowledgeItem.organization_id == org_id,
                KnowledgeItem.is_active.is_(True),
            ),
        )
        or 0,
    )
    # Шесть равных «весов»: 100% только когда закрыты все пункты (см. подсказки в админке).
    step_rows: list[tuple[str, str, bool, str, str]] = [
        (
            "iiko",
            "iiko подключён",
            iiko_ok,
            "connections",
            "«Подключения»: API-ключ iiko Cloud и сохранение с выбором организации.",
        ),
        (
            "menu",
            "Меню импортировано",
            menu_n > 0,
            "connections",
            "«Подключения»: импорт меню (после iiko — «Сохранить и импортировать» или синхронизация). Нужен хотя бы один пункт.",
        ),
        (
            "whatsapp",
            "WhatsApp (номер или .env)",
            wa_ok,
            "connections",
            "«Подключения»: Phone Number ID в форме или WHATSAPP_PHONE_NUMBER_ID и WHATSAPP_API_TOKEN в .env.",
        ),
        (
            "packaging",
            "Правила упаковки",
            packaging_n > 0,
            "restaurant",
            "«Мой ресторан»: добавьте активное правило упаковки (надбавка за контейнер и т.п.).",
        ),
        (
            "upsell",
            "Правила допродаж",
            rules_n > 0,
            "smart_sales",
            "«Умные продажи»: хотя бы одно активное правило допродаж.",
        ),
        (
            "knowledge",
            "База знаний",
            kb_n > 0,
            "restaurant",
            "«Мой ресторан», блок «База знаний»: хотя бы одна активная запись для гостей и бота.",
        ),
    ]
    steps = [
        {
            "id": sid,
            "label": label,
            "done": done,
            "open_tab": tab,
            "hint": hint,
        }
        for sid, label, done, tab, hint in step_rows
    ]
    score = int(round(100 * sum(1 for s in steps if s["done"]) / max(len(steps), 1)))

    return {
        "score": score,
        "steps": steps,
        "menu_items": menu_n,
        "packaging_rules": packaging_n,
        "upsell_rules": rules_n,
        "knowledge_items": kb_n,
    }


# ─── Integrations sync ────────────────────────────────────


@router.post("/integrations/sync")
async def integrations_sync_now(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Запуск полной синхронизации меню + стоп-листов POS в фоне (не блокирует HTTP-запрос).

    Результат пишется в слоты last_* и журнал интеграций; UI обновляет статус опросом / WS.
    """
    org_id = admin_org_from_session(request)
    import app.services.pos.adapters  # noqa: F401 — register adapters
    from app.services.pos.adapters.base import get_pos_adapter

    try:
        adapter = await get_pos_adapter(db, org_id)
        health = await adapter.health(db, org_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not health.get("ok"):
        slug = getattr(adapter, "provider_slug", "pos")
        raise HTTPException(
            status_code=400,
            detail=f"Настройте {slug} для филиала",
        )

    background_tasks.add_task(run_full_iiko_sync_for_org, int(org_id))
    snap = await build_status_payload(
        db,
        organization_id=int(org_id),
        iiko_configured=await iiko_effective_configured(db, org_id),
        whatsapp_configured=await whatsapp_effective_configured(db, org_id),
    )
    logger.info("Ручная синхронизация iiko поставлена в фон: org_id=%s", org_id)
    return {
        "ok": True,
        "mode": "background",
        "menu": {"ok": None, "stats": None, "error": None, "pending": True},
        "stop_lists": {"ok": None, "stats": None, "error": None, "pending": True},
        "status": snap,
    }


# ─── Menu CRUD ────────────────────────────────────────────


@router.get("/menu")
async def list_menu(
    request: Request,
    category: str | None = Query(None, description="Фильтр по категории"),
    available_only: bool = Query(True),
    stopped_only: bool = Query(
        False,
        description="Только позиции в стопе (is_available=false); при True игнорируется available_only",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список позиций меню."""
    org_id = admin_org_from_session(request)
    query = (
        select(MenuItem)
        .where(_menu_tenant_clause(org_id))
        .order_by(MenuItem.category, MenuItem.name)
    )
    if category:
        query = query.where(MenuItem.category == category)
    if stopped_only:
        query = query.where(MenuItem.is_available.is_(False))
    elif available_only:
        query = query.where(MenuItem.is_available.is_(True))

    result = await db.execute(query)
    items = result.scalars().all()

    if stopped_only:
        logger.info(
            "Admin stoplist fetch org=%s: returned=%d (stopped_only=true)",
            org_id,
            len(items),
        )

    return {
        "count": len(items),
        # Backward-compat: старый админский UI мог ожидать ключ `menu_items`.
        "items": [_menu_item_dict(item) for item in items],
        "menu_items": [_menu_item_dict(item) for item in items],
    }


@router.post("/menu")
async def create_menu_item(
    request: Request,
    body: MenuItemCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Добавить позицию меню вручную (iiko_id генерируется локально)."""
    org_id = admin_org_from_session(request)
    pk = (body.portion_kind or "single").strip().lower()
    if pk not in ("single", "shareable"):
        pk = "single"
    smin = max(1, min(99, int(body.serves_min)))
    smax = max(1, min(99, int(body.serves_max)))
    if smax < smin:
        smax = smin
    item = MenuItem(
        organization_id=org_id,
        name=body.name.strip(),
        category=(body.category or "").strip(),
        description=(body.description or "").strip(),
        tags=(body.tags or "").strip(),
        portion_kind=pk,
        serves_min=smin,
        serves_max=smax,
        allergens=(body.allergens or "").strip(),
        ingredients_summary=(body.ingredients_summary or "").strip(),
        dietary_tags=(body.dietary_tags or "").strip(),
        upsell_pairs=(body.upsell_pairs or "").strip(),
        price=body.price,
        cost_price=body.cost_price,
        is_available=body.is_available,
        image_url=(body.image_url or "").strip() or None,
    )
    item.iiko_id = str(uuid.uuid4())
    db.add(item)
    await db.flush()
    return {"ok": True, "item": _menu_item_dict(item)}


@router.patch("/menu/{item_id}")
async def patch_menu_item(
    request: Request,
    item_id: int,
    body: MenuItemPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Изменить поля позиции меню (цена, стоп-лист, название и т.д.)."""
    org_id = admin_org_from_session(request)
    item = await _menu_item_in_org(db, item_id, org_id)

    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].strip()
    if "description" in data and data["description"] is not None:
        data["description"] = data["description"].strip()
    if "tags" in data and data["tags"] is not None:
        data["tags"] = data["tags"].strip()
    if "portion_kind" in data and data["portion_kind"] is not None:
        pk = str(data["portion_kind"]).strip().lower()
        data["portion_kind"] = pk if pk in ("single", "shareable") else "single"
    if "serves_min" in data and data["serves_min"] is not None:
        data["serves_min"] = max(1, min(99, int(data["serves_min"])))
    if "serves_max" in data and data["serves_max"] is not None:
        data["serves_max"] = max(1, min(99, int(data["serves_max"])))
    if "allergens" in data and data["allergens"] is not None:
        data["allergens"] = str(data["allergens"]).strip()
    if "ingredients_summary" in data and data["ingredients_summary"] is not None:
        data["ingredients_summary"] = str(data["ingredients_summary"]).strip()
    if "dietary_tags" in data and data["dietary_tags"] is not None:
        data["dietary_tags"] = str(data["dietary_tags"]).strip()
    if "upsell_pairs" in data and data["upsell_pairs"] is not None:
        data["upsell_pairs"] = str(data["upsell_pairs"]).strip()
    if "image_url" in data:
        url = (data["image_url"] or "").strip()
        data["image_url"] = url if url else None
    if "cost_price" in data and data["cost_price"] is not None:
        data["cost_price"] = max(0.0, float(data["cost_price"]))

    for key, value in data.items():
        setattr(item, key, value)
    if hasattr(item, "serves_min") and hasattr(item, "serves_max"):
        if int(item.serves_max or 1) < int(item.serves_min or 1):
            item.serves_max = int(item.serves_min or 1)

    await db.flush()
    invalidate_menu_context_cache(org_id)
    return {"ok": True, "item": _menu_item_dict(item)}


async def _read_menu_cost_csv(file: UploadFile) -> str:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Файл пуст")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


@router.post("/menu/cost-import/preview")
async def preview_menu_costs(
    request: Request,
    file: UploadFile = File(..., description="CSV: iiko_id или name + cost_price"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Предпросмотр импорта себестоимости из CSV без записи в БД."""
    org_id = admin_org_from_session(request)
    text = await _read_menu_cost_csv(file)
    result = await preview_menu_costs_from_csv(db, org_id, text)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors") or "Некорректный CSV")
    return {"ok": True, "preview": True, **result}


@router.post("/menu/cost-import/apply")
async def apply_menu_costs(
    request: Request,
    file: UploadFile = File(..., description="CSV: iiko_id или name + cost_price"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Применить импорт себестоимости позиций меню из CSV."""
    org_id = admin_org_from_session(request)
    text = await _read_menu_cost_csv(file)
    result = await import_menu_costs_from_csv(db, org_id, text)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors") or "Некорректный CSV")
    invalidate_menu_context_cache(org_id)
    await db.flush()
    return {"ok": True, "applied": True, **result}


@router.post("/menu/cost-import")
async def import_menu_costs(
    request: Request,
    file: UploadFile = File(..., description="CSV: iiko_id или name + cost_price"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Импорт себестоимости позиций меню из CSV (alias apply)."""
    return await apply_menu_costs(request, file, db)


@router.post("/menu/clear")
async def clear_all_menu_items(
    request: Request,
    body: ClearMenuBody,
    _perm: None = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Удалить **все** строки из ``menu_items`` (заказы в БД не трогаются — позиции в ``items_json`` сохраняются).

    Требуется ``{"confirm": true}`` — защита от случайного вызова.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Для очистки меню передайте в теле JSON: {\"confirm\": true}",
        )
    org_id = admin_org_from_session(request)
    cnt = (
        await db.scalar(
            select(func.count()).select_from(MenuItem).where(MenuItem.organization_id == org_id),
        )
        or 0
    )
    await db.execute(sql_delete(MenuItem).where(MenuItem.organization_id == org_id))
    await db.flush()
    logger.warning("Админ: полная очистка menu_items, удалено позиций: %d", cnt)
    return {"ok": True, "deleted": int(cnt)}


@router.delete("/menu/{item_id}")
async def delete_menu_item(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Удалить позицию из меню (осторожно: старые заказы ссылаются на названия в JSON)."""
    org_id = admin_org_from_session(request)
    await _menu_item_in_org(db, item_id, org_id)
    await db.execute(sql_delete(MenuItem).where(MenuItem.id == item_id))
    await db.flush()
    return {"ok": True, "id": item_id}


@router.post("/menu/sync")
async def sync_menu(
    request: Request,
    api_login: str | None = Query(
        None,
        description="API-логин iiko (если не задан — берётся IIKO_API_LOGIN из .env)",
    ),
    organization_id: str | None = Query(
        None,
        description="ID организации в iiko (если не задан — IIKO_ORGANIZATION_ID из .env)",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Синхронизация номенклатуры iiko → ``menu_items`` (цены и UUID для бота).
    Учётные данные из query, из настроек филиала или из .env. Совпадение по ``iiko_id``.
    """
    org_id = admin_org_from_session(request)
    import app.services.pos.adapters  # noqa: F401 — register adapters
    from app.services.pos.adapters.base import get_pos_adapter

    try:
        adapter = await get_pos_adapter(db, org_id)
        stats = await adapter.sync_menu(db, org_id)
        invalidate_menu_context_cache(org_id)
        sk = stats.get("skipped")
        detail_m = (
            f"Синхронизация меню: успешно "
            f"(всего {stats.get('total', 0)}, новых {stats.get('created', 0)}, обновлено {stats.get('updated', 0)}"
            + (f", пропущено {sk}" if sk else "")
            + ")"
        )
        await record_menu_sync(
            db, True, None, detail=detail_m, organization_id=admin_org_from_session(request),
        )
        return {"ok": True, "status": "ok", **stats}
    except Exception as exc:
        err = str(exc)
        logger.error("Ошибка синхронизации меню: %s", exc, exc_info=True)
        await record_menu_sync(
            db, False, err, detail=f"Синхронизация меню: ошибка — {err[:400]}",
            organization_id=admin_org_from_session(request),
        )
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {err}")


@router.post("/menu/reindex-embeddings")
async def post_menu_reindex_embeddings(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    E12: пересчитать эмбеддинги позиций меню (нужен OPENAI_API_KEY и MENU_RAG_ENABLED на сервере для выборки в промпте).
    """
    org_id = admin_org_from_session(request)
    out = await reindex_organization_menu_embeddings(db, org_id)
    err = out.get("error")
    if err:
        raise HTTPException(status_code=502, detail=str(err))
    await db.commit()
    stats = {k: v for k, v in out.items() if k != "error"}
    return {"ok": True, "embedding_stats": stats}


@router.post("/menu/stop-lists")
async def sync_stop_lists_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Синхронизация стоп-листов для **текущего филиала** (учётка из настроек org).
    Раньше принимались произвольные api_login/organization_id (риск cross-tenant).
    """
    org_id = admin_org_from_session(request)
    import app.services.pos.adapters  # noqa: F401 — register adapters
    from app.services.pos.adapters.base import get_pos_adapter

    try:
        adapter = await get_pos_adapter(db, org_id)
        stats = await adapter.sync_stoplist(db, org_id)
        detail_s = (
            f"Стоп-листы: успешно "
            f"(в стопе: {stats.get('stopped', 0)}, восстановлено: {stats.get('restored', 0)})"
        )
        await record_stoplist_sync(
            db, True, None, detail=detail_s, organization_id=org_id,
        )
        snap = await build_status_payload(
            db,
            organization_id=int(org_id),
            iiko_configured=await iiko_effective_configured(db, org_id),
            whatsapp_configured=await whatsapp_effective_configured(db, org_id),
        )
        return {"ok": True, "status": "ok", **stats, "integration_status": snap}
    except Exception as exc:
        err = str(exc)
        logger.error("Ошибка синхронизации стоп-листов: %s", exc, exc_info=True)
        await record_stoplist_sync(
            db, False, err, detail=f"Стоп-листы: ошибка — {err[:400]}",
            organization_id=org_id,
        )
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {err}")


@router.post("/stop-lists/sync")
async def sync_stop_lists_from_env(
    request: Request,
    api_login: str | None = Query(
        None,
        description="API-логин iiko (если не задан — IIKO_API_LOGIN из .env)",
    ),
    organization_id: str | None = Query(
        None,
        description="ID организации (если не задан — IIKO_ORGANIZATION_ID из .env)",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Синхронизация стоп-листов iiko → флаги is_available в menu_items (филиал, .env или query).
    """
    org_id = admin_org_from_session(request)
    login, org, tg = await _iiko_login_org_for_tenant(db, org_id, api_login, organization_id)
    try:
        stats = await sync_stop_lists(
            db, login, org, terminal_group_id=tg, menu_organization_id=org_id,
        )
        detail_s = (
            f"Стоп-листы: успешно "
            f"(в стопе: {stats.get('stopped', 0)}, восстановлено: {stats.get('restored', 0)})"
        )
        await record_stoplist_sync(
            db, True, None, detail=detail_s, organization_id=org_id,
        )
        snap = await build_status_payload(
            db,
            organization_id=int(org_id),
            iiko_configured=await iiko_effective_configured(db, org_id),
            whatsapp_configured=await whatsapp_effective_configured(db, org_id),
        )
        logger.info("Синхронизация стоп-листов из админки: %s", stats)
        return {"ok": True, "status": "ok", **stats, "integration_status": snap}
    except Exception as exc:
        err = str(exc)
        logger.error("Ошибка синхронизации стоп-листов (.env): %s", exc, exc_info=True)
        await record_stoplist_sync(
            db, False, err, detail=f"Стоп-листы: ошибка — {err[:400]}",
            organization_id=admin_org_from_session(request),
        )
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {err}")
