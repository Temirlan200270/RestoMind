"""Шифрование чувствительных строк для хранения в БД (Fernet)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_fernet: Fernet | None = None


def fernet_or_none() -> Fernet | None:
    """Экземпляр Fernet или None, если ``APP_SECRETS_FERNET_KEY`` не задан."""
    global _fernet
    raw = (settings.app_secrets_fernet_key or "").strip()
    if not raw:
        return None
    if _fernet is None:
        _fernet = Fernet(raw.encode("utf-8"))
    return _fernet


def encrypt_secret(plain: str) -> str:
    f = fernet_or_none()
    if f is None:
        raise ValueError("Задайте APP_SECRETS_FERNET_KEY в .env для шифрования секретов")
    return f.encrypt((plain or "").encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    f = fernet_or_none()
    if f is None:
        raise ValueError("APP_SECRETS_FERNET_KEY не настроен — нельзя расшифровать")
    try:
        return f.decrypt((token or "").encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Неверный ключ шифрования или повреждены данные") from exc
