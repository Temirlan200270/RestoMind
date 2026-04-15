"""База знаний: разделение facility vs persona в блоке для промпта."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeItem, Organization
from app.services.knowledge_context import load_knowledge_context_block


@pytest.mark.asyncio
async def test_knowledge_context_two_sections(db_session: AsyncSession) -> None:
    db_session.add(Organization(id=1, name="O", slug="o"))
    await db_session.flush()
    db_session.add_all(
        [
            KnowledgeItem(
                organization_id=1,
                knowledge_kind="facility",
                category="Часы",
                question="Когда открыты?",
                answer="10–22",
                is_active=True,
            ),
            KnowledgeItem(
                organization_id=1,
                knowledge_kind="persona",
                category="Тон",
                question="Стиль",
                answer="Кратко и дружелюбно.",
                is_active=True,
            ),
        ],
    )
    await db_session.flush()
    text = await load_knowledge_context_block(db_session, 1)
    assert "Справочник заведения" in text
    assert "Часы" in text or "Когда открыты" in text
    assert "persona" in text.lower() or "Характер" in text
    assert "дружелюбно" in text
