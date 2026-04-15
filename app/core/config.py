"""
Конфигурация приложения.
Читает переменные окружения из .env файла через Pydantic BaseSettings.
"""

import os
from typing import Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Главный конфиг приложения — все секреты и параметры подключений."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
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
    # Если задан REDIS_URL (например rediss://default:TOKEN@host:6379 от Upstash) — он имеет приоритет над REDIS_HOST/PORT/DB.
    # Для Pub/Sub в админке нужен TCP-клиент (redis-py), не Upstash REST API.
    redis_url_dsn: str = Field(
        default="",
        validation_alias=AliasChoices("REDIS_URL", "redis_url_dsn"),
    )
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_enabled: bool = False
    # Только если TLS к Redis падает на проверке сертификата (редко; в проде по возможности false)
    redis_ssl_skip_verify: bool = Field(
        default=False,
        validation_alias=AliasChoices("REDIS_SSL_SKIP_VERIFY", "redis_ssl_skip_verify"),
    )
    # Принудительно in-memory: не подключаться к REDIS_URL (тесты, исчерпана квота Upstash и т.п.).
    # Имеет приоритет над REDIS_ENABLED — внешний Redis не используется.
    redis_memory_only: bool = Field(
        default=False,
        validation_alias=AliasChoices("REDIS_MEMORY_ONLY", "redis_memory_only"),
    )

    # --- OpenAI (чат + structured output; Whisper для STT; опционально OPENAI_BASE_URL) ---
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_KEY", "openai_api_key"),
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("OPENAI_MODEL", "openai_model"),
    )
    openai_transcription_model: str = Field(
        default="whisper-1",
        validation_alias=AliasChoices(
            "OPENAI_TRANSCRIPTION_MODEL",
            "openai_transcription_model",
        ),
    )
    # Пусто = api.openai.com; иначе совместимые прокси / Azure OpenAI resource
    openai_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_BASE_URL", "openai_base_url"),
    )

    # --- WhatsApp (Meta API) ---
    whatsapp_api_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = Field(
        default="v21.0",
        validation_alias=AliasChoices("WHATSAPP_API_VERSION", "whatsapp_api_version"),
    )
    # Twilio Voice (PSTN): для подписи вебхука и TwiML Say через REST (секреты только из env)
    twilio_account_sid: str = Field(
        default="",
        validation_alias=AliasChoices("TWILIO_ACCOUNT_SID", "twilio_account_sid"),
    )
    twilio_auth_token: str = Field(
        default="",
        validation_alias=AliasChoices("TWILIO_AUTH_TOKEN", "twilio_auth_token"),
    )
    # Накопление μ-law от Media Stream перед отправкой в Whisper (~8000 байт/сек моно)
    twilio_voice_buffer_bytes: int = Field(
        default=24_000,
        ge=4000,
        validation_alias=AliasChoices("TWILIO_VOICE_BUFFER_BYTES", "twilio_voice_buffer_bytes"),
    )
    # Публичный URL сайта (https://your-domain.com) — для подсказки URL вебхука в админке
    public_base_url: str = Field(default="", validation_alias=AliasChoices("PUBLIC_BASE_URL", "public_base_url"))
    # Публичная ссылка на меню/сайт — бот отдаёт её в FAQ при запросе «меню», «ссылка» и т.п.
    menu_public_url: str = Field(
        default="https://luniq.net/plovxana_pvl_1",
        validation_alias=AliasChoices("MENU_PUBLIC_URL", "menu_public_url"),
    )
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
    # IANA (например Asia/Almaty) — время в Telegram-алертах эскалации: сначала «заведение», затем UTC
    display_timezone: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DISPLAY_TIMEZONE",
            "TELEGRAM_ALERT_TIMEZONE",
            "display_timezone",
        ),
    )

    # Fernet (URL-safe base64 32-byte key): python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Нужен для шифрования IIKO apiLogin в БД (organizations.iiko_api_login_enc).
    app_secrets_fernet_key: str = Field(
        default="",
        validation_alias=AliasChoices("APP_SECRETS_FERNET_KEY", "app_secrets_fernet_key"),
    )
    # Секрет для POST /api/webhooks/payment (Authorization: Bearer …). Пусто — эндпоинт отвечает 503.
    payment_webhook_bearer_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "PAYMENT_WEBHOOK_BEARER_TOKEN",
            "payment_webhook_bearer_token",
        ),
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
    # Тип заказа для iiko deliveries/create (обязательное поле).
    # iiko требует РОВНО одно из: orderTypeId или orderServiceType.
    # Для MVP задаём orderTypeId (UUID из iiko Office / Cloud).
    iiko_order_type_id: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_ORDER_TYPE_ID", "iiko_order_type_id"),
    )
    # Раздельные типы (опционально): если заданы — имеют приоритет над IIKO_ORDER_TYPE_ID.
    iiko_order_type_id_delivery: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_ORDER_TYPE_ID_DELIVERY", "iiko_order_type_id_delivery"),
    )
    iiko_order_type_id_pickup: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_ORDER_TYPE_ID_PICKUP", "iiko_order_type_id_pickup"),
    )
    iiko_order_type_id_hall: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_ORDER_TYPE_ID_HALL", "iiko_order_type_id_hall"),
    )
    # Принудительно отправлять ВСЕ заказы из ИИ-агента как "в зале",
    # чтобы они попадали в общий операционный поток iikoFront (чекница/касса по залу).
    iiko_force_hall_for_ai_orders: bool = Field(
        default=False,
        validation_alias=AliasChoices("IIKO_FORCE_HALL_FOR_AI_ORDERS", "iiko_force_hall_for_ai_orders"),
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

    # Предоплата заказа (не бронь): сумма >= порога — клиент не может подтвердить «Да» до оплаты (статус в админке).
    order_prepayment_threshold_kzt: float = Field(
        default=15_000.0,
        validation_alias=AliasChoices(
            "ORDER_PREPAYMENT_THRESHOLD_KZT",
            "order_prepayment_threshold_kzt",
        ),
    )

    # --- Упаковка (спец. номенклатура): цены в ₸ и UUID товаров iiko для строк заказа ---
    packaging_manty_unit_price: float = Field(
        default=200.0,
        validation_alias=AliasChoices("PACKAGING_MANTY_UNIT_PRICE", "packaging_manty_unit_price"),
    )
    packaging_plov_half_unit_price: float = Field(
        default=300.0,
        validation_alias=AliasChoices("PACKAGING_PLOV_HALF_UNIT_PRICE", "packaging_plov_half_unit_price"),
    )
    packaging_plov_1kg_tabak_unit_price: float = Field(
        default=800.0,
        validation_alias=AliasChoices("PACKAGING_PLOV_1KG_TABAK_UNIT_PRICE", "packaging_plov_1kg_tabak_unit_price"),
    )
    packaging_plov_1kg_foil_unit_price: float = Field(
        default=650.0,
        validation_alias=AliasChoices("PACKAGING_PLOV_1KG_FOIL_UNIT_PRICE", "packaging_plov_1kg_foil_unit_price"),
    )
    packaging_shashlik_unit_price: float = Field(
        default=200.0,
        validation_alias=AliasChoices("PACKAGING_SHASHLIK_UNIT_PRICE", "packaging_shashlik_unit_price"),
    )
    packaging_fries_unit_price: float = Field(
        default=50.0,
        validation_alias=AliasChoices("PACKAGING_FRIES_UNIT_PRICE", "packaging_fries_unit_price"),
    )
    packaging_standard_unit_price: float = Field(
        default=150.0,
        validation_alias=AliasChoices("PACKAGING_STANDARD_UNIT_PRICE", "packaging_standard_unit_price"),
    )
    iiko_product_id_packaging_manty: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_PRODUCT_ID_PACKAGING_MANTY", "iiko_product_id_packaging_manty"),
    )
    iiko_product_id_packaging_plov_half: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_PRODUCT_ID_PACKAGING_PLOV_HALF", "iiko_product_id_packaging_plov_half"),
    )
    iiko_product_id_packaging_plov_tabak: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_PRODUCT_ID_PACKAGING_PLOV_TABAK", "iiko_product_id_packaging_plov_tabak"),
    )
    iiko_product_id_packaging_plov_foil: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_PRODUCT_ID_PACKAGING_PLOV_FOIL", "iiko_product_id_packaging_plov_foil"),
    )

    # --- Админ-панель ---
    admin_username: str = "admin"
    admin_password: str = "restomind"
    # Организация по умолчанию (fallback вебхука и legacy-логина), если в БД одна запись — обычно 1
    default_organization_id: int = Field(
        default=1,
        validation_alias=AliasChoices("DEFAULT_ORGANIZATION_ID", "default_organization_id"),
    )
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

    # --- Очередь задач (ARQ + Redis) ---
    arq_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("ARQ_ENABLED", "arq_enabled"),
    )
    arq_queue_name: str = Field(
        default="restomind",
        validation_alias=AliasChoices("ARQ_QUEUE_NAME", "arq_queue_name"),
    )

    # --- Сессии админки (cookie) и подпись WS-токена ---
    # В продакшене задайте длинную случайную строку (openssl rand -hex 32)
    session_secret: str = ""

    @model_validator(mode="after")
    def _postgres_if_database_url(self) -> Self:
        """Managed Postgres передаёт DATABASE_URL — принудительно включаем postgres."""
        if self.database_url_dsn.strip():
            object.__setattr__(self, "db_mode", "postgres")
        return self

    @model_validator(mode="after")
    def _redis_memory_only_overrides_enabled(self) -> Self:
        """REDIS_MEMORY_ONLY — отключает внешний Redis независимо от REDIS_ENABLED."""
        if self.redis_memory_only:
            object.__setattr__(self, "redis_enabled", False)
        return self

    @model_validator(mode="after")
    def _public_base_url_from_render(self) -> Self:
        """На Render задаётся RENDER_EXTERNAL_URL — подставляем, если PUBLIC_BASE_URL пуст (вебхуки, ссылки в Telegram)."""
        raw = (self.public_base_url or "").strip()
        if raw:
            return self
        ext = (os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
        if ext:
            object.__setattr__(self, "public_base_url", ext)
        return self

    @model_validator(mode="after")
    def _require_strong_admin_secrets_in_prod(self) -> Self:
        """
        В продакшене SessionMiddleware и WS-токены должны подписываться настоящим секретом,
        а не производным от дефолтных кредов.

        Чтобы не ломать локальную разработку "из коробки", включаем правило только если
        похоже на прод-среду (Render / Postgres / DATABASE_URL).
        """
        is_prod_like = bool(
            (os.environ.get("RENDER") or "").strip()
            or self.database_url_dsn.strip()
            or (self.db_mode or "").strip().lower() == "postgres"
        )
        if not self.app_debug and is_prod_like:
            if not self.session_secret.strip():
                raise ValueError("SESSION_SECRET обязателен в продакшене (задайте длинную случайную строку).")
            if (self.admin_username or "").strip().lower() == "admin" and (self.admin_password or "").strip() == "restomind":
                raise ValueError("ADMIN_USERNAME/ADMIN_PASSWORD должны быть изменены в продакшене (дефолтные небезопасны).")
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
        """URL для подключения к Redis (локально или managed: Render KV, Upstash rediss://, и т.д.)."""
        raw = self.redis_url_dsn.strip()
        if raw:
            return raw
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


# Синглтон — используем один экземпляр во всём приложении
settings = Settings()
