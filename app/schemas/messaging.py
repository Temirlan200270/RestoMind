from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChannelSender(BaseModel):
    external_id: str = ""
    phone: str = ""
    display_name: str = ""


class ChannelMessageContent(BaseModel):
    type: str = "text"
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelInboundEvent(BaseModel):
    trace_id: str = ""
    correlation_id: str = ""
    idempotency_key: str = ""
    provider: str
    channel_connection_id: int
    external_chat_id: str
    external_message_id: str = ""
    sender: ChannelSender = Field(default_factory=ChannelSender)
    message: ChannelMessageContent
    received_at: datetime | None = None


class ChannelOutboundCommand(BaseModel):
    trace_id: str = ""
    correlation_id: str = ""
    idempotency_key: str
    provider: str
    channel_connection_id: int
    conversation_id: int | None = None
    external_chat_id: str
    message: ChannelMessageContent


class ChannelConnectionStatusEvent(BaseModel):
    channel_connection_id: int
    provider: str = "whatsapp_baileys"
    status: str
    phone: str = ""
    display_name: str = ""
    external_account_id: str = ""
    session_ref: str = ""
    qr: str = ""
    health: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ChannelDeliveryEvent(BaseModel):
    channel_message_id: int | None = None
    channel_connection_id: int | None = None
    provider: str = ""
    external_message_id: str = ""
    status: Literal["sent", "delivered", "read", "failed"] | str
    error_code: str = ""
    error_message: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class ChannelConnectionCreate(BaseModel):
    provider: str = "whatsapp_baileys"
    phone: str = ""
    display_name: str = ""


class ChannelConnectionOut(BaseModel):
    id: int
    organization_id: int
    provider: str
    status: str
    external_account_id: str = ""
    phone: str = ""
    display_name: str = ""
    session_ref: str = ""
    is_default_outbound: bool = False
    last_qr: str = ""
    health: dict[str, Any] = Field(default_factory=dict)
    last_error: str = ""
    last_seen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChannelMessageOut(BaseModel):
    id: int
    organization_id: int
    conversation_id: int | None = None
    channel_connection_id: int
    chat_log_id: int | None = None
    trace_id: str = ""
    correlation_id: str = ""
    provider: str
    direction: str
    external_chat_id: str = ""
    external_message_id: str = ""
    idempotency_key: str
    status: str
    message_type: str = "text"
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    created_at: datetime | None = None
