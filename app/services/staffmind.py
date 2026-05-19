"""StaffMind MVP: WhatsApp-ready onboarding using the knowledge base."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeItem, StaffOnboardingSession


async def start_onboarding_session(
    db: AsyncSession,
    org_id: int,
    *,
    phone: str,
    role: str = "staff",
    staff_user_id: int | None = None,
) -> StaffOnboardingSession:
    session = StaffOnboardingSession(
        organization_id=org_id,
        staff_user_id=staff_user_id,
        phone=phone.strip(),
        role=role.strip() or "staff",
        status="active",
        current_step=0,
        progress_json={"completed_topics": []},
    )
    db.add(session)
    await db.flush()
    return session


async def answer_staff_question(
    db: AsyncSession,
    session: StaffOnboardingSession,
    question: str,
) -> str:
    q = (question or "").strip()
    session.last_question = q
    if not q:
        answer = "Напишите вопрос по работе, меню, смене или стандартам сервиса."
        session.last_answer = answer
        return answer

    rows = (await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.is_active.is_(True),
            or_(KnowledgeItem.organization_id == session.organization_id, KnowledgeItem.organization_id.is_(None)),
        )
        .order_by(KnowledgeItem.sort_order.asc(), KnowledgeItem.id.asc())
        .limit(100)
    )).scalars().all()
    q_low = q.lower()
    best = None
    best_score = 0
    for item in rows:
        hay = f"{item.category} {item.question} {item.answer}".lower()
        score = sum(1 for token in q_low.split() if len(token) >= 3 and token in hay)
        if score > best_score:
            best = item
            best_score = score
    if best is None:
        answer = (
            "Я не нашёл точный ответ в базе знаний. Зафиксируйте вопрос для наставника "
            "и добавьте ответ в Knowledge Base, чтобы следующий сотрудник получил его автоматически."
        )
    else:
        answer = best.answer
        progress = dict(session.progress_json or {})
        topics = list(progress.get("completed_topics") or [])
        topic = best.category or best.question
        if topic and topic not in topics:
            topics.append(topic)
        progress["completed_topics"] = topics
        session.progress_json = progress
        session.current_step = len(topics)
    session.last_answer = answer
    return answer


def onboarding_public(row: StaffOnboardingSession) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "organization_id": int(row.organization_id),
        "staff_user_id": row.staff_user_id,
        "phone": row.phone,
        "role": row.role,
        "status": row.status,
        "current_step": int(row.current_step or 0),
        "progress": row.progress_json or {},
        "last_question": row.last_question,
        "last_answer": row.last_answer,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
