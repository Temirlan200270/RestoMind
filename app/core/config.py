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
    # Дублировать ответ бота голосом (edge-tts, бесплатно) после текста — только если клиент прислал голос
    whatsapp_voice_replies: bool = Field(
        default=False,
        validation_alias=AliasChoices("WHATSAPP_VOICE_REPLIES", "whatsapp_voice_replies"),
    )
    # Голос Microsoft Edge TTS (ru-RU-SvetlanaNeural, и др.)
    edge_tts_voice: str = Field(
        default="ru-RU-SvetlanaNeural",
        validation_alias=AliasChoices("EDGE_TTS_VOICE", "edge_tts_voice"),
    )

    # --- Telegram (оповещения при эскалации на оператора) ---
    telegram_bot_token: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "telegram_bot_token"),
    )
    telegram_admin_chat_id: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_ADMIN_CHAT_ID", "telegram_admin_chat_id"),
    )

    # --- iiko Cloud API ---
    iiko_api_login: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_API_LOGIN", "iiko_api_login"),
    )
    iiko_organization_id: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_ORGANIZATION_ID", "iiko_organization_id"),
    )
    # Терминальная группа (касса/точка): доставки в iiko + фильтр стоп-листа по одной точке (сеть)
    iiko_terminal_group_id: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_TERMINAL_GROUP_ID", "iiko_terminal_group_id"),
    )
    # UUID товаров в iiko для автострок (можно один общий контейнер или раздельно по ТЗ)
    iiko_product_id_container: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_PRODUCT_ID_CONTAINER", "iiko_product_id_container"),
        description="Общий UUID контейнера, если не заданы отдельные для зала / доставки",
    )
    iiko_product_id_container_hall: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_PRODUCT_ID_CONTAINER_HALL", "iiko_product_id_container_hall"),
    )
    iiko_product_id_container_delivery_pickup: str = Field(
        default="",
        validation_alias=AliasChoices(
            "IIKO_PRODUCT_ID_CONTAINER_DELIVERY",
            "IIKO_PRODUCT_ID_CONTAINER_DELIVERY_PICKUP",
            "iiko_product_id_container_delivery_pickup",
        ),
    )
    iiko_product_id_delivery: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_PRODUCT_ID_DELIVERY", "iiko_product_id_delivery"),
    )
    # Синхронизация меню: только позиции с type Dish/Good (отсекает модификаторы и пр.; при False — все продукты)
    iiko_menu_sync_only_dish_good: bool = Field(
        default=False,
        validation_alias=AliasChoices("IIKO_MENU_SYNC_ONLY_DISH_GOOD", "iiko_menu_sync_only_dish_good"),
    )

    # --- Тарификация заказов (v2.0) — тенге ---
    pricing_container_hall: float = Field(
        default=0.0,
        validation_alias=AliasChoices("PRICING_CONTAINER_HALL", "pricing_container_hall"),
        description="Цена контейнера при заказе «в зале» (0 = бесплатно)",
    )
    pricing_container_delivery_pickup: float = Field(
        default=250.0,
        validation_alias=AliasChoices("PRICING_CONTAINER_DELIVERY_PICKUP", "pricing_container_delivery_pickup"),
        description="Цена контейнера при доставке или самовывозе",
    )
    pricing_delivery_fee: float = Field(
        default=700.0,
        validation_alias=AliasChoices(
            "PRICING_DELIVERY_FEE", "pricing_delivery_fee", "DELIVERY_FEE", "delivery_fee",
        ),
        description="Плата за доставку при сумме блюд ниже порога (ТЗ: 700 ₸)",
    )
    pricing_delivery_free_threshold: float = Field(
        default=10_000.0,
        validation_alias=AliasChoices(
            "PRICING_DELIVERY_FREE_THRESHOLD",
            "pricing_delivery_free_threshold",
            "DELIVERY_FREE_THRESHOLD",
            "delivery_free_threshold",
        ),
        description="Сумма блюд, от которой доставка 0 ₸ (ТЗ: 10 000)",
    )
    hall_prepayment_min: int = Field(
        default=5000,
        validation_alias=AliasChoices("HALL_PREPAYMENT_MIN", "hall_prepayment_min"),
        description="Минимальная предоплата за бронь в зале (ТЗ), текстом для клиента",
    )
    pricing_containers_per_main_unit: float = Field(
        default=1.0,
        validation_alias=AliasChoices("PRICING_CONTAINERS_PER_MAIN_UNIT", "pricing_containers_per_main_unit"),
        description="Сколько контейнеров на 1 порцию основного блюда (обычно 1)",
    )

    # --- Админ-панель ---
    admin_username: str = "admin"
    admin_password: str = "restomind"
    # Версия для отображения в админке (можно переопределить при деплое)
    app_version: str = Field(
        default="0.1.0",
        validation_alias=AliasChoices("APP_VERSION", "app_version"),
    )
    # Ретеншн chat_logs: 0 = выключено; иначе удалять записи старше N суток (фоновая задача)
    chat_log_retention_days: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("CHAT_LOG_RETENTION_DAYS", "chat_log_retention_days"),
    )
    chat_log_retention_interval_seconds: int = Field(
        default=86_400,
        ge=60,
        validation_alias=AliasChoices(
            "CHAT_LOG_RETENTION_INTERVAL_SECONDS",
            "chat_log_retention_interval_seconds",
        ),
    )

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
