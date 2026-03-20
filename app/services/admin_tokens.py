"""
Подписанные токены для WebSocket админки (без передачи пароля в query).
"""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

_WS_MAX_AGE_SEC = 86400  # 24 ч
_SALT = "restomind-admin-ws-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret_key,
        salt=_SALT,
    )


def create_admin_ws_token(username: str) -> str:
    """Выдать подписанный токен для подключения к /api/admin/ws."""
    return _serializer().dumps({"u": username})


def parse_admin_ws_token(token: str) -> str | None:
    """Проверить токен, вернуть username или None."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=_WS_MAX_AGE_SEC)
        u = data.get("u")
        return str(u) if u else None
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
