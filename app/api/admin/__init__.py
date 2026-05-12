"""
Пакет админ-API. Роуты собраны в `_monolith.py` до завершения раскола E0.1 по подмодулям.
"""

from ._monolith import (
    LoginBody,
    _check_mixed_payment_split,
    _ws_event_allowed_for_org,
    admin_ai_value,
    admin_demo_login,
    admin_incidents,
    admin_login,
    admin_order_timeline,
    admin_readiness,
    analytics,
    auth_router,
    require_admin_session,
    require_admin_session_active,
    retry_failed_task,
    router,
    ws_router,
)
from .analytics import admin_readiness
from .orders import admin_order_timeline
from .chats import resend_failed_chat_message, send_message

__all__ = [
    "LoginBody",
    "_check_mixed_payment_split",
    "_ws_event_allowed_for_org",
    "admin_ai_value",
    "admin_demo_login",
    "admin_incidents",
    "admin_login",
    "admin_order_timeline",
    "admin_readiness",
    "analytics",
    "auth_router",
    "require_admin_session",
    "require_admin_session_active",
    "resend_failed_chat_message",
    "retry_failed_task",
    "router",
    "send_message",
    "ws_router",
]
