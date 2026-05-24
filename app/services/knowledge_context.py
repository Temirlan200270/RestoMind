"""
Загрузка базы знаний для подстановки в промпт LLM (CONTEXT_KB).
"""

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeItem

logger = logging.getLogger(__name__)

# Ограничение размера блока в символах (меню отдельно; суммарный контекст не раздуваем).
MAX_KB_CONTEXT_CHARS = 14_000


async def load_knowledge_context_block(
    db: AsyncSession,
    organization_id: int | None,
) -> str:
    """
    Возвращает текстовый блок для system instruction: только is_active.
    - organization_id is None: только записи с organization_id IS NULL (глобальные).
    - иначе: глобальные OR записи этой организации.
    """
    if organization_id is None:
        stmt = (
            select(KnowledgeItem)
            .where(KnowledgeItem.is_active.is_(True))
            .where(KnowledgeItem.organization_id.is_(None))
            .order_by(KnowledgeItem.sort_order, KnowledgeItem.id)
        )
    else:
        from app.services.tenant_scope import legacy_null_org_visible

        org_filters = [KnowledgeItem.organization_id == organization_id]
        if legacy_null_org_visible(organization_id):
            org_filters.append(KnowledgeItem.organization_id.is_(None))
        stmt = (
            select(KnowledgeItem)
            .where(KnowledgeItem.is_active.is_(True))
            .where(or_(*org_filters))
            .order_by(KnowledgeItem.sort_order, KnowledgeItem.id)
        )

    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    if not rows:
        return ""

    def _kind(row: KnowledgeItem) -> str:
        k = (row.knowledge_kind or "facility").strip().lower()
        return "persona" if k == "persona" else "facility"

    facility_rows = [r for r in rows if _kind(r) == "facility"]
    persona_rows = [r for r in rows if _kind(r) == "persona"]

    def _build_section(title: str, section_rows: list[KnowledgeItem]) -> str:
        if not section_rows:
            return ""
        parts: list[str] = [f"## {title}\n"]
        total = len(parts[0])
        for row in section_rows:
            cat = (row.category or "").strip()
            q = (row.question or "").strip()
            a = (row.answer or "").strip()
            header = f"### [{cat}] {q}" if cat else f"### {q}"
            block = f"{header}\n{a}\n"
            if total + len(block) > MAX_KB_CONTEXT_CHARS:
                logger.warning(
                    "База знаний обрезана по лимиту %s символов",
                    MAX_KB_CONTEXT_CHARS,
                )
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts).strip()

    fac = _build_section("Справочник заведения", facility_rows)
    per = _build_section("Характер и тон ответов (persona)", persona_rows)
    return "\n\n".join(x for x in (fac, per) if x).strip()
