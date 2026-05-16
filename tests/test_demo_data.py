import pytest
from sqlalchemy import func, select

from app.db.models import (
    AiUsageLog,
    Booking,
    BusinessRecommendation,
    ChatLog,
    EscalationEvent,
    FailedTask,
    IntelligenceConversation,
    IntelligenceMessage,
    OperationalInsight,
    Order,
    PaymentEvent,
    PipelineLatencyLog,
    RestaurantStateSnapshot,
    SystemEvent,
    User,
    Organization,
)
from app.services.demo_data import clear_demo_data, seed_demo_data


@pytest.mark.asyncio
async def test_seed_and_clear_demo_data_cover_extended_entities(db_session):
    org = Organization(name="Demo Org", slug="demo-org")
    db_session.add(org)
    await db_session.flush()

    stats = await seed_demo_data(db_session, organization_id=int(org.id))
    assert stats["skipped"] is False
    assert stats["users_created"] == 10
    assert stats["orders_added"] > 0
    assert stats["payment_events_added"] > 0
    assert stats["system_events_added"] > 0
    assert stats["insights_added"] > 0
    assert stats["recommendations_added"] > 0

    async def count(model, *where):
        return int(
            await db_session.scalar(
                select(func.count()).select_from(model).where(*where)
            )
            or 0
        )

    assert await count(User, User.organization_id == int(org.id)) == 10
    assert await count(Order, Order.organization_id == int(org.id)) == stats["orders_added"]
    assert await count(Booking, Booking.organization_id == int(org.id)) == stats["bookings_added"]
    assert await count(ChatLog, ChatLog.organization_id == int(org.id)) == stats["chat_logs_added"]
    assert await count(PaymentEvent) == stats["payment_events_added"]
    assert await count(SystemEvent, SystemEvent.organization_id == int(org.id)) == stats["system_events_added"]
    assert await count(EscalationEvent, EscalationEvent.organization_id == int(org.id)) == stats["escalations_added"]
    assert await count(FailedTask, FailedTask.organization_id == int(org.id)) == stats["failed_tasks_added"]
    assert await count(AiUsageLog, AiUsageLog.organization_id == int(org.id)) == stats["ai_usage_rows_added"]
    assert await count(PipelineLatencyLog, PipelineLatencyLog.organization_id == int(org.id)) == stats["latency_rows_added"]
    assert await count(OperationalInsight, OperationalInsight.organization_id == int(org.id)) == stats["insights_added"]
    assert await count(BusinessRecommendation, BusinessRecommendation.organization_id == int(org.id)) == stats["recommendations_added"]
    assert await count(RestaurantStateSnapshot, RestaurantStateSnapshot.organization_id == int(org.id)) >= 1
    assert await count(IntelligenceConversation, IntelligenceConversation.organization_id == int(org.id)) >= 1
    assert await count(IntelligenceMessage, IntelligenceMessage.organization_id == int(org.id)) >= 2

    cleared = await clear_demo_data(db_session, organization_id=int(org.id))
    assert cleared["users_deleted"] == 10
    assert cleared["orders_deleted"] == stats["orders_added"]
    assert cleared["bookings_deleted"] == stats["bookings_added"]
    assert cleared["chat_logs_deleted"] == stats["chat_logs_added"]
    assert cleared["intelligence_messages_deleted"] >= 2

    assert await count(User, User.organization_id == int(org.id)) == 0
    assert await count(Order, Order.organization_id == int(org.id)) == 0
    assert await count(Booking, Booking.organization_id == int(org.id)) == 0
    assert await count(ChatLog, ChatLog.organization_id == int(org.id)) == 0
    assert await count(PaymentEvent) == 0
    assert await count(SystemEvent, SystemEvent.organization_id == int(org.id)) == 0
    assert await count(EscalationEvent, EscalationEvent.organization_id == int(org.id)) == 0
    assert await count(FailedTask, FailedTask.organization_id == int(org.id)) == 0
    assert await count(AiUsageLog, AiUsageLog.organization_id == int(org.id)) == 0
    assert await count(PipelineLatencyLog, PipelineLatencyLog.organization_id == int(org.id)) == 0
    assert await count(OperationalInsight, OperationalInsight.organization_id == int(org.id)) == 0
    assert await count(BusinessRecommendation, BusinessRecommendation.organization_id == int(org.id)) == 0
    assert await count(RestaurantStateSnapshot, RestaurantStateSnapshot.organization_id == int(org.id)) == 0
    assert await count(IntelligenceConversation, IntelligenceConversation.organization_id == int(org.id)) == 0
    assert await count(IntelligenceMessage, IntelligenceMessage.organization_id == int(org.id)) == 0
