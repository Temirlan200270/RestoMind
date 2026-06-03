"""
E12: эмбеддинги позиций меню для семантического top-k в промпте.

Вектор хранится как float32 little-endian в MenuItem.embedding (BYTEA).
Поиск — косинусное сходство в Python (до десятков тысяч позиций достаточно).
"""

from __future__ import annotations

import logging
import math
import struct
from typing import Sequence

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import MenuItem

logger = logging.getLogger(__name__)

_EMB_BATCH = 64


def menu_item_embed_text(item: MenuItem) -> str:
    """Текст для embedding: название, категория, описание, теги."""
    parts: list[str] = []
    name = (item.name or "").strip()
    if name:
        parts.append(name)
    cat = (item.category or "").strip()
    if cat:
        parts.append(f"Категория: {cat}")
    desc = (item.description or "").strip()
    if desc:
        parts.append(desc[:500])
    tags = (item.tags or "").strip()
    if tags:
        parts.append(f"Теги: {tags}")
    ing = (getattr(item, "ingredients_summary", None) or "").strip()
    if ing:
        parts.append(f"Состав: {ing[:200]}")
    return "\n".join(parts) if parts else name or "menu item"


def vec_from_bytes(data: bytes | None) -> list[float] | None:
    if not data or len(data) < 16:
        return None
    n = len(data) // 4
    if n < 1:
        return None
    try:
        return list(struct.unpack(f"<{n}f", data[: n * 4]))
    except struct.error:
        return None


def vec_to_bytes(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return -1.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _ensure_openai_client() -> AsyncOpenAI | None:
    try:
        from app.services.ai_brain import _ensure_openai_client

        return _ensure_openai_client()
    except Exception:
        key = (settings.openai_api_key or "").strip()
        if not key:
            return None
        kw: dict[str, object] = {"api_key": key}
        base = (settings.openai_base_url or "").strip()
        if base:
            kw["base_url"] = base
        return AsyncOpenAI(**kw)


async def embed_texts(client: AsyncOpenAI, texts: list[str]) -> list[list[float]]:
    """Батч эмбеддингов; сохраняет порядок."""
    model = (settings.openai_embedding_model or "").strip() or "text-embedding-3-small"
    out: list[list[float]] = []
    for i in range(0, len(texts), _EMB_BATCH):
        chunk = texts[i : i + _EMB_BATCH]
        resp = await client.embeddings.create(model=model, input=chunk)
        data = getattr(resp, "data", None) or []
        for row in sorted(data, key=lambda d: getattr(d, "index", 0)):
            v = getattr(row, "embedding", None)
            if isinstance(v, list):
                out.append([float(x) for x in v])
            else:
                out.append([])
    return out


async def reindex_organization_menu_embeddings(
    db: AsyncSession,
    organization_id: int,
) -> dict[str, int | str | None]:
    """
    Пересчитать эмбеддинги для всех доступных позиций филиала.
    """
    client = _ensure_openai_client()
    if client is None:
        return {"total_items": 0, "updated": 0, "error": "OPENAI_API_KEY не задан"}

    oid = int(organization_id)
    stmt = select(MenuItem).where(
        MenuItem.organization_id == oid,
        MenuItem.is_available.is_(True),
        MenuItem.is_archived.is_(False),
    ).order_by(MenuItem.id)
    result = await db.execute(stmt)
    items = list(result.scalars().all())
    if not items:
        return {"total_items": 0, "updated": 0, "error": None}

    texts = [menu_item_embed_text(it) for it in items]
    updated = 0
    try:
        vectors = await embed_texts(client, texts)
    except Exception as exc:
        logger.exception("menu embeddings batch failed: %s", exc)
        return {"total_items": len(items), "updated": 0, "error": str(exc)[:500]}

    if len(vectors) != len(items):
        return {"total_items": len(items), "updated": 0, "error": "embedding count mismatch"}

    for it, vec in zip(items, vectors):
        if not vec:
            continue
        it.embedding = vec_to_bytes(vec)
        updated += 1

    await db.flush()
    return {"total_items": len(items), "updated": updated, "error": None}


async def embed_query_vector(query: str) -> list[float] | None:
    q = (query or "").strip()
    if not q:
        return None
    client = _ensure_openai_client()
    if client is None:
        return None
    vecs = await embed_texts(client, [q])
    return vecs[0] if vecs and vecs[0] else None


def top_k_menu_items_by_similarity(
    menu_items: list[MenuItem],
    query_vec: list[float],
    *,
    top_k: int,
) -> list[MenuItem]:
    """Косинусный top-k среди позиций с непустым embedding."""
    scored: list[tuple[float, MenuItem]] = []
    for item in menu_items:
        vec = vec_from_bytes(item.embedding)
        if vec is None or len(vec) != len(query_vec):
            continue
        sim = cosine_similarity(query_vec, vec)
        scored.append((sim, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:top_k]]
