"""
Конфигурация приложения.
Читает переменные окружения из .env файла через Pydantic BaseSettings.
"""

from typing import Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Главный конфиг приложения — все секреты и параметры подключений."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- Приложение ---
    app_name: str = "RestoMind"
    app_debug: bool = False

    # --- Режим базы данных ---
    # "sqlite" — работает без установки (по умолчанию для разработки)
    # "postgres" — для продакшена
    db_mode: str = "sqlite"

    # --- Полный DSN PostgreSQL (Render, Railway и др. задают DATABASE_URL) ---
    # Если задан — используется вместо сборки из postgres_*; режим БД становится postgres.
    database_url_dsn: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_URL", "database_url_dsn"),
    )

    # --- PostgreSQL (нужен только при db_mode=postgres) ---
    postgres_user: str = "restomind"
    postgres_password: str = "restomind_secret"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "restomind_db"

    # --- Redis (опционально) ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_enabled: bool = False

    # --- Gemini (Google AI) ---
    gemini_api_key: str = ""

    # --- WhatsApp (Meta API) ---
    whatsapp_api_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_phone_number_id: str = ""
    # Публичный URL сайта (https://your-domain.com) — для подсказки URL вебхука в админке
    public_base_url: str = Field(default="", validation_alias=AliasChoices("PUBLIC_BASE_URL", "public_base_url"))

    # --- Telegram (оповещения при эскалации на оператора) ---
    telegram_bot_token: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "telegram_bot_token"),
    )
    telegram_admin_chat_id: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_ADMIN_CHAT_ID", "telegram_admin_chat_id"),
    )

    # --- iiko Cloud API ---
    iiko_api_login: str = ""
    iiko_organization_id: str = ""

    # --- Админ-панель ---
    admin_username: str = "admin"
    admin_password: str = "restomind"

    # --- Sentry (опционально) ---
    sentry_dsn: str = ""

    # --- Rate Limiting ---
    rate_limit_per_minute: int = 20

    # --- Сессии админки (cookie) и подпись WS-токена ---
    # В продакшене задайте длинную случайную строку (openssl rand -hex 32)
    session_secret: str = ""

    @model_validator(mode="after")
    def _postgres_if_database_url(self) -> Self:
        """Managed Postgres передаёт DATABASE_URL — принудительно включаем postgres."""
        if self.database_url_dsn.strip():
            object.__setattr__(self, "db_mode", "postgres")
        return self

    @property
    def session_secret_key(self) -> str:
        """Секрет для SessionMiddleware и подписи ws_token."""
        if self.session_secret.strip():
            return self.session_secret.strip()
        import hashlib
        raw = f"{self.app_name}:{self.admin_username}:{self.admin_password}:restomind-session"
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def database_url(self) -> str:
        """DSN для подключения к БД — SQLite или PostgreSQL в зависимости от режима."""
        raw = self.database_url_dsn.strip()
        if raw:
            # Render и др.: postgresql:// или postgres://
            if raw.startswith("postgres://"):
                rest = raw[len("postgres://") :]
                return f"postgresql+asyncpg://{rest}"
            if raw.startswith("postgresql://") and not raw.startswith("postgresql+asyncpg"):
                rest = raw[len("postgresql://") :]
                return f"postgresql+asyncpg://{rest}"
            return raw
        if self.db_mode == "postgres":
            return (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return "sqlite+aiosqlite:///restomind.db"

    @property
    def redis_url(self) -> str:
        """URL для подключения к Redis."""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


# Синглтон — используем один экземпляр во всём приложении
settings = Settings()
