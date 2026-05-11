from datetime import datetime, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin import retry_failed_task
from app.db.models import FailedTask, Organization


class DummyRequest:
    def __init__(self, session: dict):
        self.session = session


@pytest.mark.asyncio
async def test_retry_failed_task_enqueues_and_resolves(db_session, monkeypatch) -> None:
    org = Organization(id=501, name="Retry Org", slug="retry-org")
    task = FailedTask(
        organization_id=501,
        phone="+77001112233",
        message_text="Хочу плов",
        error="boom",
        attempts=3,
        resolved=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([org, task])
    await db_session.flush()

    calls: list[dict] = []

    async def fake_dispatch(job_name, background_tasks, **kwargs):
        calls.append({"job_name": job_name, **kwargs})

    monkeypatch.setattr("app.services.task_queue.dispatch_arq_or_background", fake_dispatch)

    req = DummyRequest({"admin_ok": True, "organization_id": 501})
    out = await retry_failed_task(req, int(task.id), BackgroundTasks(), db_session)

    assert out["ok"] is True
    assert out["queued"] == "arq"
    assert out["task"]["resolved"] is True
    assert calls == [
        {
            "job_name": "whatsapp_process_text",
            "phone": "+77001112233",
            "message_text": "Хочу плов",
            "whatsapp_message_id": "",
            "webhook_value": None,
            "organization_id": 501,
        },
    ]


@pytest.mark.asyncio
async def test_retry_failed_task_respects_tenant_scope(db_session, monkeypatch) -> None:
    db_session.add_all([
        Organization(id=601, name="Org A", slug="org-a"),
        Organization(id=602, name="Org B", slug="org-b"),
    ])
    task = FailedTask(
        organization_id=601,
        phone="+77001112233",
        message_text="hello",
        error="boom",
        attempts=3,
        resolved=False,
    )
    db_session.add(task)
    await db_session.flush()

    async def fake_dispatch(*args, **kwargs):  # pragma: no cover
        raise AssertionError("must not enqueue out-of-scope task")

    monkeypatch.setattr("app.services.task_queue.dispatch_arq_or_background", fake_dispatch)

    req = DummyRequest({"admin_ok": True, "organization_id": 602})
    with pytest.raises(HTTPException) as exc:
        await retry_failed_task(req, int(task.id), BackgroundTasks(), db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_retry_failed_task_rejects_resolved(db_session, monkeypatch) -> None:
    org = Organization(id=701, name="Done Org", slug="done-org")
    task = FailedTask(
        organization_id=701,
        phone="+77001112233",
        message_text="hello",
        error="boom",
        attempts=3,
        resolved=True,
    )
    db_session.add_all([org, task])
    await db_session.flush()

    async def fake_dispatch(*args, **kwargs):  # pragma: no cover
        raise AssertionError("resolved task should not enqueue")

    monkeypatch.setattr("app.services.task_queue.dispatch_arq_or_background", fake_dispatch)

    req = DummyRequest({"admin_ok": True, "organization_id": 701})
    with pytest.raises(HTTPException) as exc:
        await retry_failed_task(req, int(task.id), BackgroundTasks(), db_session)
    assert exc.value.status_code == 409
