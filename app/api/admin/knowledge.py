"""
База знаний (FAQ/persona) — CRUD-эндпоинты админки.

E0.1: вынесено из ``app/api/admin/_monolith.py`` без изменения поведения.
Контракт путей и форма ответа совпадают с предыдущей версией; зависимости
(сессия, org-scope) перенесены без правок логики.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import KnowledgeItem
from app.db.session import get_db

from .deps import (
    _knowledge_tenant_clause,
    admin_org_from_session,
    require_admin_session_active,
)

logger = logging.getLogger(__name__)

knowledge_router = APIRouter(dependencies=[Depends(require_admin_session_active)])


def _knowledge_item_dict(row: KnowledgeItem) -> dict:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "knowledge_kind": getattr(row, "knowledge_kind", None) or "facility",
        "category": row.category or "",
        "question": row.question,
        "answer": row.answer,
        "is_active": row.is_active,
        "sort_order": row.sort_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class KnowledgeItemCreateBody(BaseModel):
    category: str = Field(default="", max_length=120)
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=50_000)
    is_active: bool = True
    sort_order: int = Field(0, ge=-10_000, le=10_000)
    organization_id: int | None = Field(
        None,
        description="Игнорируется: запись создаётся в филиале текущей сессии (multi-tenant).",
    )
    knowledge_kind: str = Field(
        "facility",
        description="facility — справочник заведения; persona — тон и характер бота",
    )


class KnowledgeItemPatchBody(BaseModel):
    category: str | None = Field(None, max_length=120)
    question: str | None = Field(None, min_length=1, max_length=500)
    answer: str | None = Field(None, min_length=1, max_length=50_000)
    is_active: bool | None = None
    sort_order: int | None = Field(None, ge=-10_000, le=10_000)
    organization_id: int | None = None
    knowledge_kind: str | None = Field(None, description="facility | persona")


@knowledge_router.get("/knowledge")
async def list_knowledge_items(
    request: Request,
    db: AsyncSession = Depends(get_db),
    active_only: bool = Query(False, description="Только is_active=true"),
) -> dict:
    """Список записей базы знаний для админки."""
    org_id = admin_org_from_session(request)
    q = (
        select(KnowledgeItem)
        .where(_knowledge_tenant_clause(org_id))
        .order_by(KnowledgeItem.sort_order, KnowledgeItem.id)
    )
    if active_only:
        q = q.where(KnowledgeItem.is_active.is_(True))
    result = await db.execute(q)
    rows = list(result.scalars().all())
    return {"items": [_knowledge_item_dict(r) for r in rows]}


@knowledge_router.post("/knowledge")
async def create_knowledge_item(
    request: Request,
    body: KnowledgeItemCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    kk_raw = (body.knowledge_kind or "facility").strip().lower()
    kk = "persona" if kk_raw == "persona" else "facility"
    row = KnowledgeItem(
        organization_id=org_id,
        knowledge_kind=kk[:32],
        category=(body.category or "").strip(),
        question=body.question.strip(),
        answer=body.answer.strip(),
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    db.add(row)
    await db.flush()
    return {"ok": True, "item": _knowledge_item_dict(row)}


@knowledge_router.patch("/knowledge/{item_id}")
async def patch_knowledge_item(
    request: Request,
    item_id: int,
    body: KnowledgeItemPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(KnowledgeItem, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.organization_id is None and org_id != int(settings.default_organization_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.organization_id is not None and int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    data = body.model_dump(exclude_unset=True)
    # Запрет переноса записи между филиалами через PATCH.
    data.pop("organization_id", None)
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].strip()
    if "question" in data and data["question"] is not None:
        data["question"] = data["question"].strip()
    if "answer" in data and data["answer"] is not None:
        data["answer"] = data["answer"].strip()
    if "knowledge_kind" in data and data["knowledge_kind"] is not None:
        kk = str(data["knowledge_kind"]).strip().lower()
        data["knowledge_kind"] = kk if kk == "persona" else "facility"
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    return {"ok": True, "item": _knowledge_item_dict(row)}


@knowledge_router.delete("/knowledge/{item_id}")
async def delete_knowledge_item(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _delete_knowledge_item_impl(request, item_id, db)


@knowledge_router.post("/knowledge/{item_id}/delete")
async def delete_knowledge_item_post(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """То же, что DELETE /knowledge/{id}: часть хостингов/прокси режет метод DELETE."""
    return await _delete_knowledge_item_impl(request, item_id, db)


async def _delete_knowledge_item_impl(request: Request, item_id: int, db: AsyncSession) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(KnowledgeItem, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.organization_id is None and org_id != int(settings.default_organization_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.organization_id is not None and int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    await db.execute(sql_delete(KnowledgeItem).where(KnowledgeItem.id == item_id))
    await db.flush()
    return {"ok": True, "id": item_id}
