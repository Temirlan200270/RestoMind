from __future__ import annotations

import os
import ssl
from urllib.parse import urlparse

from app.db.pool_settings import is_supabase_transaction_pooler


def build_postgres_ssl_context() -> ssl.SSLContext:
    """
    SSLContext для asyncpg через SQLAlchemy connect_args.

    Supabase pooler иногда отдаёт цепочку, которую строгая верификация Python/OpenSSL
    может отклонить как «self-signed certificate in certificate chain».
    Поэтому:
    - по умолчанию пытаемся использовать certifi CA bundle;
    - добавляем VERIFY_X509_PARTIAL_CHAIN (если доступно) — это часто чинит промежуточные цепочки;
    - опционально можно отключить проверку через env `DATABASE_SSL_SKIP_VERIFY=true` (не рекомендуется в проде).
    """
    ctx = ssl.create_default_context()

    # certifi — самый предсказуемый CA bundle внутри slim Docker образов
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
    except Exception:
        # Если certifi недоступен — остаёмся на системном trust store
        pass

    # Python 3.10+: частичная цепочка сертификатов (полезно для некоторых CDN/LB цепочек)
    verify_partial = getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", None)
    if verify_partial is not None:
        try:
            ctx.verify_flags |= int(verify_partial)
        except Exception:
            pass

    if (os.environ.get("DATABASE_SSL_SKIP_VERIFY") or "").strip().lower() in {"1", "true", "yes"}:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    return ctx


def postgres_connect_args(database_url: str) -> dict:
    """
    connect_args для Postgres DSN.

    Важно: не используем `?sslmode=` в URL — asyncpg/SQLAlchemy могут превращать это в kwargs.
    """
    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("postgres"):
        return {}

    args: dict = {}
    # Local/CI Postgres usually runs without TLS; asyncpg fails if we force an
    # SSL context against localhost. Managed Postgres keeps the SSL default.
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        args["ssl"] = build_postgres_ssl_context()
    # Supabase transaction pooler (PgBouncer) не поддерживает prepared statements asyncpg.
    if is_supabase_transaction_pooler(database_url):
        args["statement_cache_size"] = 0
        args["prepared_statement_cache_size"] = 0
    return args
