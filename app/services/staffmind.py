"""StaffMind MVP: WhatsApp-ready onboarding using the knowledge base."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeItem, StaffOnboardingSession

DEFAULT_STEP_TARGET = 5


async def _kb_step_target(db: AsyncSession, org_id: int) -> int:
    """Distinct active KB categories/topics — onboarding progress denominator."""
    count = await db.scalar(
        select(func.count(func.distinct(KnowledgeItem.category))).where(
            KnowledgeItem.is_active.is_(True),
            or_(KnowledgeItem.organization_id == org_id, KnowledgeItem.organization_id.is_(None)),
            KnowledgeItem.category.isnot(None),
            KnowledgeItem.category != "",
        )
    )
    n = int(count or 0)
    return max(DEFAULT_STEP_TARGET, n)


def _progress_dict(raw: dict[str, Any] | None) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


async def _ensure_progress_metrics(
    db: AsyncSession,
    session: StaffOnboardingSession,
) -> dict[str, Any]:
    progress = _progress_dict(session.progress_json)
    if "step_target" not in progress:
        progress["step_target"] = await _kb_step_target(db, session.organization_id)
    progress.setdefault("questions_asked", 0)
    progress.setdefault("completed_topics", [])
    topics = list(progress.get("completed_topics") or [])
    step_target = int(progress.get("step_target") or DEFAULT_STEP_TARGET)
    progress["test_passed"] = bool(
        progress.get("test_passed")
        or str(session.status or "").lower() == "completed"
        or (len(topics) >= step_target and int(progress.get("questions_asked") or 0) > 0)
    )
    session.progress_json = progress
    return progress


async def start_onboarding_session(
    db: AsyncSession,
    org_id: int,
    *,
    phone: str,
    role: str = "staff",
    staff_user_id: int | None = None,
) -> StaffOnboardingSession:
    step_target = await _kb_step_target(db, org_id)
    session = StaffOnboardingSession(
        organization_id=org_id,
        staff_user_id=staff_user_id,
        phone=phone.strip(),
        role=role.strip() or "staff",
        status="active",
        current_step=0,
        progress_json={
            "completed_topics": [],
            "questions_asked": 0,
            "step_target": step_target,
            "test_passed": False,
        },
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
    progress = await _ensure_progress_metrics(db, session)
    if q:
        progress["questions_asked"] = int(progress.get("questions_asked") or 0) + 1
        session.progress_json = progress
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
        topics = list(progress.get("completed_topics") or [])
        topic = best.category or best.question
        if topic and topic not in topics:
            topics.append(topic)
        progress["completed_topics"] = topics
        session.current_step = len(topics)
        step_target = int(progress.get("step_target") or DEFAULT_STEP_TARGET)
        if len(topics) >= step_target and int(progress.get("questions_asked") or 0) > 0:
            progress["test_passed"] = True
        session.progress_json = progress
    session.last_answer = answer
    return answer


def onboarding_public(row: StaffOnboardingSession) -> dict[str, Any]:
    progress = _progress_dict(row.progress_json)
    topics = list(progress.get("completed_topics") or [])
    step_target = int(progress.get("step_target") or max(DEFAULT_STEP_TARGET, len(topics)))
    questions_asked = int(progress.get("questions_asked") or 0)
    test_passed = bool(
        progress.get("test_passed")
        or str(row.status or "").lower() == "completed"
        or (len(topics) >= step_target and questions_asked > 0)
    )
    progress.setdefault("step_target", step_target)
    progress.setdefault("questions_asked", questions_asked)
    progress["test_passed"] = test_passed
    return {
        "id": int(row.id),
        "organization_id": int(row.organization_id),
        "staff_user_id": row.staff_user_id,
        "phone": row.phone,
        "role": row.role,
        "status": row.status,
        "current_step": int(row.current_step or 0),
        "progress": progress,
        "questions_asked": questions_asked,
        "step_target": step_target,
        "test_passed": test_passed,
        "last_question": row.last_question,
        "last_answer": row.last_answer,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
