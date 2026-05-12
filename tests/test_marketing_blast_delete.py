"""Удаление рассылки: recipients + blast без NULL blast_id (регрессия IntegrityError)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.api.admin.marketing import delete_blast
from app.db.models import MarketingBlast, MarketingBlastRecipient, Organization


@pytest.mark.asyncio
async def test_delete_blast_core_delete_recipients_first(db_session) -> None:
    org = Organization(name="MBlast Org", slug="mblast-org")
    db_session.add(org)
    await db_session.flush()
    blast = MarketingBlast(
        organization_id=int(org.id),
        name="Кампания",
        segment_type="all_active",
        message_text="Текст рассылки не короче пяти символов.",
        status="draft",
    )
    db_session.add(blast)
    await db_session.flush()
    bid = int(blast.id)
    db_session.add(
        MarketingBlastRecipient(
            blast_id=bid,
            user_id=None,
            phone="+77001112233",
            status="sent",
        ),
    )
    await db_session.flush()

    class _Req:
        session = {"admin_ok": True, "organization_id": int(org.id)}

    out = await delete_blast(bid, _Req(), db_session)
    assert out["ok"] is True

    n_left = int(
        await db_session.scalar(select(func.count()).select_from(MarketingBlast).where(MarketingBlast.id == bid))
        or 0,
    )
    n_rec = int(
        await db_session.scalar(
            select(func.count()).select_from(MarketingBlastRecipient).where(MarketingBlastRecipient.blast_id == bid),
        )
        or 0,
    )
    assert n_left == 0
    assert n_rec == 0
