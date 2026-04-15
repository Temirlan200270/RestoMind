"""
Подписанные токены для WebSocket админки (без передачи пароля в query).
Полезная нагрузка: organization_id, опционально staff_id и email.
"""

from __future__ import annotations

from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

_WS_MAX_AGE_SEC = 86400  # 24 ч
_SALT = "restomind-admin-ws-v2"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret_key,
        salt=_SALT,
    )


@dataclass(frozen=True)
class AdminWsClaims:
    """Распарсенный ws_token."""

    email: str
    organization_id: int
    staff_id: int | None


def create_admin_ws_token(
    *,
    organization_id: int,
    email: str = "",
    staff_id: int | None = None,
) -> str:
    """Выдать подписанный токен для подключения к /api/admin/ws."""
    payload: dict = {
        "u": email or "",
        "oid": int(organization_id),
    }
    if staff_id is not None:
        payload["sid"] = int(staff_id)
    return _serializer().dumps(payload)


def parse_admin_ws_token(token: str) -> AdminWsClaims | None:
    """Проверить токен; вернуть claims или None."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=_WS_MAX_AGE_SEC)
        oid = data.get("oid")
        if oid is None:
            return None
        sid = data.get("sid")
        return AdminWsClaims(
            email=str(data.get("u") or ""),
            organization_id=int(oid),
            staff_id=int(sid) if sid is not None else None,
        )
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
