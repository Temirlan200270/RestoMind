"""
Пакет админ-API. E0.1 завершён: маршруты вынесены в подмодули;
_monolith.py теперь compatibility shim; legacy protected REST живёт в legacy_ops.py.
"""

from ._monolith import (
    _check_mixed_payment_split,
    require_admin_session,
    require_admin_session_active,
    router,
)
from .analytics import admin_ai_value, admin_incidents, admin_readiness, analytics
from .auth import LoginBody, admin_demo_login, admin_login, auth_router
from .chats import resend_failed_chat_message, send_message
from .orders import admin_order_timeline, retry_failed_task
from .ws import _ws_event_allowed_for_org, ws_router

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
