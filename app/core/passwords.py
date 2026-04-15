"""Хеширование паролей staff без внешних зависимостей (PBKDF2-HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_password(plain: str, *, iterations: int = 310_000) -> str:
    """Строка формата pbkdf2_sha256$iter$salt_b64$hash_b64 для хранения в БД."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt,
        iterations,
    )
    import base64

    return (
        "pbkdf2_sha256$"
        + str(iterations)
        + "$"
        + base64.b64encode(salt).decode("ascii")
        + "$"
        + base64.b64encode(dk).decode("ascii")
    )


def verify_password(plain: str, stored: str) -> bool:
    """Проверка пароля против строки из hash_password."""
    if not plain or not stored or not stored.startswith("pbkdf2_sha256$"):
        return False
    parts = stored.split("$")
    if len(parts) != 4:
        return False
    try:
        iterations = int(parts[1])
    except ValueError:
        return False
    import base64

    try:
        salt = base64.b64decode(parts[2].encode("ascii"))
        want = base64.b64decode(parts[3].encode("ascii"))
    except (ValueError, TypeError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(got, want)
