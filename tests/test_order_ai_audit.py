"""QA auto-audit: scoring, tenant scope, status transitions, SystemEvent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.admin import owner_intelligence_audits as audits_api
from app.db.models import (
    AiOrderAudit,
    ChatLog,
    Location,
    Order,
    OrderStatus,
    Organization,
    StaffRole,
    StaffUser,
    SystemEvent,
    User,
)
from app.services.order_ai_audit import (
    TAG_PROBABILITY,
    _org_calibration_memory,
    _prevented_value,
    apply_outcome_calibration,
    build_order_ai_audit,
    list_order_ai_audits,
    mark_order_ai_audit_status,
    record_audit_outcome_for_calibration,
    score_order_ai_risk,
    summarize_order_ai_audits,
)


class DummyRequest:
    def __init__(self, organization_id: int, staff_id: int | None = None) -> None:
        self.session: dict = {"admin_ok": True, "organization_id": organization_id}
        if staff_id is not None:
            self.session["staff_id"] = staff_id


def _order(
    *,
    org_id: int = 1,
    user_id: int = 1,
    location_id: int | None = None,
    status: str = OrderStatus.CONFIRMED.value,
    total_price: float = 5000.0,
    items_json: dict | None = None,
    prepayment_status: str = "not_required",
) -> Order:
    return Order(
        organization_id=org_id,
        user_id=user_id,
        location_id=location_id,
        status=status,
        total_price=total_price,
        prepayment_status=prepayment_status,
        items_json=items_json or {
            "items": [{"name": "Плов", "quantity": 1, "item_total": total_price}],
            "order_meta": {"order_type": "pickup"},
        },
    )


def test_prevented_value_uses_risk_weight_and_tag_probability() -> None:
    order = _order(total_price=10000.0)
    value = _prevented_value(order, ["manual_edit_after_ai"], "high")
    # 10000 × 0.7 × 0.75 = 5250
    assert value == 5250.0


def test_score_stoplist_conflict_confirmed_is_critical() -> None:
    order = _order(status=OrderStatus.CONFIRMED.value)
    scored = score_order_ai_risk(
        order,
        [],
        [],
        {"stoplist_items": ["Лагман"]},
    )
    assert "stoplist_conflict" in scored["tags"]
    assert scored["risk_level"] == "critical"
    assert scored["risk_score"] >= 45


def test_score_manual_edit_after_ai_is_high() -> None:
    order = _order(
        items_json={
            "items": [{"name": "Плов", "quantity": 1, "item_total": 5000}],
            "order_meta": {"order_type": "pickup", "manual_edit_after_ai": True},
        },
    )
    scored = score_order_ai_risk(order, [], [], {})
    assert "manual_edit_after_ai" in scored["tags"]
    assert scored["risk_level"] == "high"


def test_score_angry_guest_and_low_confidence_tags() -> None:
    order = _order(
        items_json={
            "items": [{"name": "Плов", "quantity": 1, "item_total": 5000}],
            "order_meta": {
                "order_type": "pickup",
                "confidence": {
                    "low_confidence": True,
                    "reasons": ["fuzzy_menu_match"],
                    "details": {},
                },
            },
        },
    )
    chat = ChatLog(
        organization_id=1,
        user_id=1,
        role="user",
        content="Это ужас, сколько ещё ждать?!",
    )
    scored = score_order_ai_risk(order, [chat], [], {})
    assert "angry_guest" in scored["tags"]
    assert "low_confidence" in scored["tags"]
    assert scored["risk_level"] in {"medium", "high"}


def test_score_wrong_address_risk_for_unverified_delivery() -> None:
    order = _order(
        items_json={
            "items": [{"name": "Плов", "quantity": 1, "item_total": 5000}],
            "order_meta": {
                "order_type": "delivery",
                "delivery_address": "ул. Абая 10",
            },
        },
    )
    scored = score_order_ai_risk(order, [], [], {})
    assert "wrong_address_risk" in scored["tags"]
    assert scored["risk_level"] == "medium"


@pytest.mark.asyncio
async def test_build_order_ai_audit_emits_system_event_on_high_risk(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    user = User(organization_id=1, phone="+77000000001")
    db_session.add_all([org, user])
    await db_session.flush()

    order = _order(
        user_id=int(user.id),
        status=OrderStatus.CONFIRMED.value,
        total_price=8000.0,
        items_json={
            "items": [{"name": "Плов", "quantity": 1, "item_total": 8000}],
            "order_meta": {
                "order_type": "pickup",
                "stoplist_items": ["Салат"],
            },
        },
    )
    db_session.add(order)
    await db_session.flush()

    audit = await build_order_ai_audit(db_session, int(order.id))
    await db_session.flush()

    assert audit.risk_level == "critical"
    assert audit.status == "open"
    assert float(audit.prevented_value) == 8000.0

    ev = await db_session.scalar(
        select(SystemEvent).where(SystemEvent.event_type == "ai_order.audit_risk_detected"),
    )
    assert ev is not None
    assert int(ev.organization_id) == 1
    payload = ev.payload_json or {}
    assert payload.get("audit_id") == int(audit.id)
    assert payload.get("risk_level") == "critical"


@pytest.mark.asyncio
async def test_mark_order_ai_audit_review_reasons(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    staff = StaffUser(
        organization_id=1,
        email="qa@org.kz",
        password_hash="hash",
        role=StaffRole.ADMIN.value,
        is_active=True,
    )
    db_session.add_all([org, staff])
    await db_session.flush()

    for idx, reason in enumerate(("no_error", "fixed", "escalated_to_manager")):
        audit = AiOrderAudit(
            organization_id=1,
            order_id=100 + idx,
            risk_score=40,
            risk_level="high",
            status="open",
        )
        db_session.add(audit)
        await db_session.flush()

        row = await mark_order_ai_audit_status(
            db_session,
            int(audit.id),
            1,
            "reviewed",
            int(staff.id),
            review_reason=reason,
        )
        assert row.review_reason == reason


@pytest.mark.asyncio
async def test_mark_order_ai_audit_status_transitions(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    staff = StaffUser(
        organization_id=1,
        email="mgr@org.kz",
        password_hash="hash",
        role=StaffRole.ADMIN.value,
        is_active=True,
    )
    db_session.add_all([org, staff])
    await db_session.flush()

    audit = AiOrderAudit(
        organization_id=1,
        order_id=99,
        risk_score=40,
        risk_level="high",
        tags_json=["manual_edit_after_ai"],
        reasons_json=["test"],
        status="open",
    )
    db_session.add(audit)
    await db_session.flush()

    reviewed = await mark_order_ai_audit_status(
        db_session,
        int(audit.id),
        1,
        "reviewed",
        int(staff.id),
    )
    assert reviewed.status == "reviewed"
    assert reviewed.reviewed_by_staff_id == int(staff.id)
    assert reviewed.reviewed_at is not None

    dismissed = await mark_order_ai_audit_status(
        db_session,
        int(audit.id),
        1,
        "dismissed",
        int(staff.id),
    )
    assert dismissed.status == "dismissed"

    with pytest.raises(ValueError, match="invalid_transition"):
        await mark_order_ai_audit_status(
            db_session,
            int(audit.id),
            1,
            "open",
            int(staff.id),
        )


@pytest.mark.asyncio
async def test_list_order_ai_audits_tenant_and_location_scope(db_session) -> None:
    org1 = Organization(id=1, name="Org1", slug="org1")
    org2 = Organization(id=2, name="Org2", slug="org2")
    loc_a = Location(organization_id=1, name="A", slug="a", is_active=True)
    loc_b = Location(organization_id=1, name="B", slug="b", is_active=True)
    db_session.add_all([org1, org2, loc_a, loc_b])
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add_all([
        AiOrderAudit(
            organization_id=1,
            location_id=int(loc_a.id),
            order_id=1,
            risk_score=30,
            risk_level="medium",
            status="open",
            created_at=now,
        ),
        AiOrderAudit(
            organization_id=1,
            location_id=int(loc_b.id),
            order_id=2,
            risk_score=50,
            risk_level="high",
            status="open",
            created_at=now,
        ),
        AiOrderAudit(
            organization_id=2,
            order_id=3,
            risk_score=99,
            risk_level="critical",
            status="open",
            created_at=now,
        ),
    ])
    await db_session.flush()

    org1_rows = await list_order_ai_audits(
        db_session,
        1,
        status="open",
        period="today",
        allowed_location_ids={int(loc_a.id)},
    )
    assert len(org1_rows) == 1
    assert int(org1_rows[0].location_id or 0) == int(loc_a.id)

    org2_rows = await list_order_ai_audits(db_session, 2, status="open", period="today")
    assert len(org2_rows) == 1
    assert int(org2_rows[0].organization_id) == 2


@pytest.mark.asyncio
async def test_api_review_and_dismiss_are_org_scoped(db_session) -> None:
    org1 = Organization(id=1, name="Org1", slug="org1")
    org2 = Organization(id=2, name="Org2", slug="org2")
    staff = StaffUser(
        organization_id=1,
        email="owner@org.kz",
        password_hash="hash",
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=True,
    )
    db_session.add_all([org1, org2, staff])
    await db_session.flush()

    audit = AiOrderAudit(
        organization_id=2,
        order_id=10,
        risk_score=20,
        risk_level="medium",
        status="open",
    )
    db_session.add(audit)
    await db_session.flush()

    req = DummyRequest(organization_id=1, staff_id=int(staff.id))
    with pytest.raises(Exception) as exc_info:
        await audits_api.review_order_ai_audit(int(audit.id), req, None, db_session)
    assert getattr(exc_info.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_api_list_order_audits_returns_items(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    staff = StaffUser(
        organization_id=1,
        email="owner@org.kz",
        password_hash="hash",
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=True,
    )
    db_session.add_all([org, staff])
    await db_session.flush()

    db_session.add(
        AiOrderAudit(
            organization_id=1,
            order_id=5,
            risk_score=25,
            risk_level="medium",
            tags_json=["payment_risk"],
            reasons_json=["pending prepay"],
            status="open",
            created_at=datetime.now(timezone.utc),
        ),
    )
    await db_session.flush()

    req = DummyRequest(organization_id=1, staff_id=int(staff.id))
    data = await audits_api.get_order_ai_audits(
        req,
        db_session,
        status="open",
        period="today",
        risk_level=None,
        tags=None,
        unreviewed_only=False,
        location_id=None,
        order_id=None,
        limit=20,
    )
    assert data["ok"] is True
    assert data["count"] == 1
    assert data["items"][0]["tags"] == ["payment_risk"]


@pytest.mark.asyncio
async def test_score_escalation_and_payment_risk_from_events() -> None:
    order = _order(prepayment_status="pending", status=OrderStatus.CONFIRMED.value)
    now = datetime.now(timezone.utc)
    events = [
        SystemEvent(
            organization_id=1,
            event_type="ai.escalated",
            source="ai",
            entity_type="user",
            entity_id="1",
            payload_json={"reason": "guest angry"},
            created_at=now,
        ),
    ]
    scored = score_order_ai_risk(order, [], events, {})
    assert "escalation_required" in scored["tags"]
    assert "payment_risk" in scored["tags"]


@pytest.mark.asyncio
async def test_build_order_ai_audit_updates_existing_open_row(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    user = User(organization_id=1, phone="+77000000002")
    db_session.add_all([org, user])
    await db_session.flush()

    order = _order(
        user_id=int(user.id),
        items_json={
            "items": [{"name": "Плов", "quantity": 1, "item_total": 3000}],
            "order_meta": {"order_type": "pickup", "manual_edit_after_ai": True},
        },
    )
    db_session.add(order)
    await db_session.flush()

    first = await build_order_ai_audit(db_session, int(order.id))
    first_id = int(first.id)

    order.items_json = {
        "items": [{"name": "Плов", "quantity": 1, "item_total": 3000}],
        "order_meta": {
            "order_type": "delivery",
            "delivery_address": "ул. Тест 1",
        },
    }
    await db_session.flush()

    second = await build_order_ai_audit(db_session, int(order.id))
    assert int(second.id) == first_id
    assert "wrong_address_risk" in (second.tags_json or [])


@pytest.mark.asyncio
async def test_list_order_ai_audits_filters_tags_risk_unreviewed(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    db_session.add(org)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add_all([
        AiOrderAudit(
            organization_id=1,
            order_id=1,
            risk_score=80,
            risk_level="critical",
            tags_json=["stoplist_conflict"],
            status="open",
            created_at=now,
        ),
        AiOrderAudit(
            organization_id=1,
            order_id=2,
            risk_score=25,
            risk_level="medium",
            tags_json=["wrong_address_risk"],
            status="reviewed",
            created_at=now,
        ),
        AiOrderAudit(
            organization_id=1,
            order_id=3,
            risk_score=55,
            risk_level="high",
            tags_json=["manual_edit_after_ai"],
            status="open",
            created_at=now,
        ),
    ])
    await db_session.flush()

    stoplist_rows = await list_order_ai_audits(
        db_session,
        1,
        status="all",
        period="today",
        tags="stoplist_conflict",
    )
    assert len(stoplist_rows) == 1
    assert stoplist_rows[0].order_id == 1

    high_rows = await list_order_ai_audits(
        db_session,
        1,
        status="all",
        period="today",
        risk_level="high,critical",
    )
    assert len(high_rows) == 2
    assert {int(r.order_id or 0) for r in high_rows} == {1, 3}

    open_rows = await list_order_ai_audits(
        db_session,
        1,
        period="today",
        unreviewed_only=True,
    )
    assert len(open_rows) == 2
    assert all((r.status or "") == "open" for r in open_rows)


@pytest.mark.asyncio
async def test_summarize_order_ai_audits_counts(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    db_session.add(org)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add_all([
        AiOrderAudit(
            organization_id=1,
            order_id=10,
            risk_score=70,
            risk_level="critical",
            tags_json=["stoplist_conflict"],
            status="open",
            created_at=now,
        ),
        AiOrderAudit(
            organization_id=1,
            order_id=11,
            risk_score=50,
            risk_level="high",
            tags_json=["wrong_address_risk"],
            status="open",
            created_at=now,
        ),
    ])
    await db_session.flush()

    summary = await summarize_order_ai_audits(
        db_session,
        1,
        status="all",
        period="today",
        risk_level="high,critical",
        unreviewed_only=True,
    )
    assert summary["total"] == 2
    assert summary["open_count"] == 2
    assert summary["critical_count"] == 1
    assert summary["high_count"] == 1


@pytest.mark.asyncio
async def test_api_order_audits_summary_endpoint(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    staff = StaffUser(
        organization_id=1,
        email="qa-summary@org.kz",
        password_hash="hash",
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=True,
    )
    db_session.add_all([org, staff])
    await db_session.flush()

    db_session.add(
        AiOrderAudit(
            organization_id=1,
            order_id=20,
            risk_score=45,
            risk_level="high",
            tags_json=["stoplist_conflict"],
            status="open",
            created_at=datetime.now(timezone.utc),
        ),
    )
    await db_session.flush()

    req = DummyRequest(organization_id=1, staff_id=int(staff.id))
    data = await audits_api.get_order_ai_audits_summary(
        req,
        db_session,
        status="open",
        period="today",
        risk_level="high,critical",
        tags="stoplist_conflict",
        unreviewed_only=True,
        location_id=None,
        order_id=None,
    )
    assert data["ok"] is True
    assert data["total"] == 1
    assert data["open_count"] == 1


@pytest.mark.asyncio
async def test_record_audit_outcome_calibration_lowers_no_error(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    db_session.add(org)
    await db_session.flush()

    _org_calibration_memory.clear()
    audit = AiOrderAudit(
        organization_id=1,
        order_id=30,
        risk_score=45,
        risk_level="high",
        tags_json=["stoplist_conflict"],
        status="reviewed",
        review_reason="no_error",
    )
    db_session.add(audit)
    await db_session.flush()

    await record_audit_outcome_for_calibration(db_session, 1, audit)
    cal = _org_calibration_memory.get(1, {})
    assert cal["stoplist_conflict"] < TAG_PROBABILITY["stoplist_conflict"]

    hints = apply_outcome_calibration(["stoplist_conflict"], "no_error", org_id=1)
    assert hints["tag_probabilities"]["stoplist_conflict"] < TAG_PROBABILITY["stoplist_conflict"]
    assert hints["calibration_adjusted"] is True


def test_prevented_value_uses_calibrated_tag_probability() -> None:
    order = _order(total_price=10000.0)
    default_value = _prevented_value(order, ["stoplist_conflict"], "critical")
    calibrated_value = _prevented_value(
        order,
        ["stoplist_conflict"],
        "critical",
        org_calibration={"stoplist_conflict": 0.1},
    )

    assert calibrated_value < default_value
    assert calibrated_value == 1000.0


@pytest.mark.asyncio
async def test_mark_reviewed_triggers_calibration(db_session) -> None:
    org = Organization(id=1, name="Org", slug="org")
    staff = StaffUser(
        organization_id=1,
        email="cal@org.kz",
        password_hash="hash",
        role=StaffRole.ADMIN.value,
        is_active=True,
    )
    db_session.add_all([org, staff])
    await db_session.flush()

    _org_calibration_memory.clear()
    audit = AiOrderAudit(
        organization_id=1,
        order_id=40,
        risk_score=45,
        risk_level="high",
        tags_json=["wrong_address_risk"],
        status="open",
    )
    db_session.add(audit)
    await db_session.flush()

    await mark_order_ai_audit_status(
        db_session,
        int(audit.id),
        1,
        "reviewed",
        int(staff.id),
        review_reason="no_error",
    )
    cal = _org_calibration_memory.get(1, {})
    assert cal["wrong_address_risk"] < TAG_PROBABILITY["wrong_address_risk"]
