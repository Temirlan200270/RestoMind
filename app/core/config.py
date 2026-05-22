"""
Конфигурация приложения.
Читает переменные окружения из .env файла через Pydantic BaseSettings.
"""

import os
import logging
from typing import Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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
    # Режим развёртывания: от него зависят обязательные секреты (webhook оплаты, WhatsApp), не от app_debug.
    # Локально оставьте development (по умолчанию). В проде/staging: production | prod | staging.
    app_environment: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT", "app_environment"),
    )

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
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias=AliasChoices("OPENAI_EMBEDDING_MODEL", "openai_embedding_model"),
    )
    # Потолок длины ответа (structured output AIBrainResponse) — снижает p95 и стоимость.
    ai_openai_max_completion_tokens: int = Field(
        default=2048,
        ge=256,
        le=16_384,
        validation_alias=AliasChoices(
            "AI_OPENAI_MAX_COMPLETION_TOKENS",
            "ai_openai_max_completion_tokens",
        ),
    )
    # E12: семантический срез меню в system prompt при большом каталоге
    menu_rag_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MENU_RAG_ENABLED", "menu_rag_enabled"),
    )
    menu_rag_min_items: int = Field(
        default=200,
        ge=1,
        le=50_000,
        validation_alias=AliasChoices("MENU_RAG_MIN_ITEMS", "menu_rag_min_items"),
    )
    menu_rag_top_k: int = Field(
        default=50,
        ge=5,
        le=200,
        validation_alias=AliasChoices("MENU_RAG_TOP_K", "menu_rag_top_k"),
    )
    # E12: Smart Category Filter — загружать только категорию из запроса + upsell-позиции
    # при большом меню (>= menu_smart_filter_min_items). Без embeddings, без векторной БД.
    menu_smart_filter_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("MENU_SMART_FILTER_ENABLED", "menu_smart_filter_enabled"),
    )
    menu_smart_filter_min_items: int = Field(
        default=60,
        ge=1,
        le=50_000,
        validation_alias=AliasChoices("MENU_SMART_FILTER_MIN_ITEMS", "menu_smart_filter_min_items"),
    )

    # --- AI Engine v2 (provider switch + Gemini) ---
    ai_provider: str = Field(
        default="openai",
        validation_alias=AliasChoices("AI_PROVIDER", "ai_provider"),
        description="openai | gemini",
    )
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GEMINI_API_KEY", "gemini_api_key"),
    )
    # Heuristic routing: FAQ/greeting → fast model; draft/order → strong (Decision Engine unchanged).
    ai_model_routing_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("AI_MODEL_ROUTING_ENABLED", "ai_model_routing_enabled"),
    )
    ai_fast_model_openai: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("AI_FAST_MODEL_OPENAI", "ai_fast_model_openai"),
    )
    ai_fast_model_gemini: str = Field(
        default="gemini-2.5-flash",
        validation_alias=AliasChoices("AI_FAST_MODEL_GEMINI", "ai_fast_model_gemini"),
    )
    # WhatsApp UX: typing indicator / fast ack after N секунд обработки (не блокирует LLM).
    bot_slow_ack_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("BOT_SLOW_ACK_ENABLED", "bot_slow_ack_enabled"),
    )
    bot_slow_ack_delay_sec: float = Field(
        default=2.0,
        ge=0.5,
        le=10.0,
        validation_alias=AliasChoices("BOT_SLOW_ACK_DELAY_SEC", "bot_slow_ack_delay_sec"),
    )
    bot_slow_ack_message: str = Field(
        default="Секунду, смотрю меню…",
        validation_alias=AliasChoices("BOT_SLOW_ACK_MESSAGE", "bot_slow_ack_message"),
    )

    # --- WhatsApp (Meta API) ---
    whatsapp_api_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_phone_number_id: str = ""
    # App Secret из кабинета Meta (WhatsApp / приложение): подпись входящих POST X-Hub-Signature-256.
    whatsapp_app_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WHATSAPP_APP_SECRET",
            "META_APP_SECRET",
            "whatsapp_app_secret",
        ),
    )
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
    # E.164 номер Twilio Voice (fallback маршрутизации, если в org.meta_json нет twilio_voice_number)
    twilio_voice_number: str = Field(
        default="",
        validation_alias=AliasChoices("TWILIO_VOICE_NUMBER", "twilio_voice_number"),
    )
    # OpenAI Realtime Voice (bidirectional Twilio Media Stream)
    openai_realtime_model: str = Field(
        default="gpt-4o-realtime-preview-2024-12-17",
        validation_alias=AliasChoices("OPENAI_REALTIME_MODEL", "openai_realtime_model"),
    )
    openai_realtime_voice: str = Field(
        default="alloy",
        validation_alias=AliasChoices("OPENAI_REALTIME_VOICE", "openai_realtime_voice"),
    )
    voice_realtime_max_session_sec: int = Field(
        default=300,
        ge=30,
        le=3600,
        validation_alias=AliasChoices(
            "VOICE_REALTIME_MAX_SESSION_SEC",
            "voice_realtime_max_session_sec",
        ),
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
    # Короткое «спасибо» в CHATTING без вызова LLM (безопасный fast-path).
    whatsapp_fast_ack_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "WHATSAPP_FAST_ACK_ENABLED",
            "whatsapp_fast_ack_enabled",
        ),
    )
    # Разбивка rm_stage_ms по этапам inbound WhatsApp (dedupe → … → reply).
    pipeline_timing_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PIPELINE_TIMING_ENABLED",
            "pipeline_timing_enabled",
        ),
    )
    pipeline_latency_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("PIPELINE_LATENCY_ENABLED", "pipeline_latency_enabled"),
    )
    sla_llm_p95_ms: int = Field(
        default=4000,
        ge=500,
        validation_alias=AliasChoices("SLA_LLM_P95_MS", "sla_llm_p95_ms"),
    )
    sla_total_p95_ms: int = Field(
        default=8000,
        ge=1000,
        validation_alias=AliasChoices("SLA_TOTAL_P95_MS", "sla_total_p95_ms"),
    )
    # TTL строки меню в Redis (ключ rm:menu_ctx:{org_id}); in-process кэш остаётся в order_logic.
    restaurant_menu_ctx_redis_ttl_sec: int = Field(
        default=90,
        ge=30,
        le=3600,
        validation_alias=AliasChoices(
            "RESTAURANT_MENU_CTX_REDIS_TTL_SEC",
            "restaurant_menu_ctx_redis_ttl_sec",
        ),
    )
    # Phase 13.1: имена одобренных в Meta шаблонов (пусто — сценарий не шлёт template из кода).
    whatsapp_template_order_receipt: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WHATSAPP_TEMPLATE_ORDER_RECEIPT",
            "whatsapp_template_order_receipt",
        ),
    )
    whatsapp_template_order_status_buttons: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WHATSAPP_TEMPLATE_ORDER_STATUS_BUTTONS",
            "whatsapp_template_order_status_buttons",
        ),
    )
    whatsapp_template_language: str = Field(
        default="ru",
        validation_alias=AliasChoices(
            "WHATSAPP_TEMPLATE_LANGUAGE",
            "whatsapp_template_language",
        ),
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
    superadmin_telegram_chat_id: str = Field(
        default="",
        validation_alias=AliasChoices("SUPERADMIN_TELEGRAM_CHAT_ID", "superadmin_telegram_chat_id"),
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

    # Telegram webhook: секрет для верификации входящих update от Telegram (X-Telegram-Bot-Api-Secret-Token)
    telegram_webhook_secret: str = Field(
        default="",
        validation_alias=AliasChoices("TELEGRAM_WEBHOOK_SECRET", "telegram_webhook_secret"),
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
    # HMAC-SHA256(raw POST body, hex) в X-RestoMind-Payment-Signature. В production (app_debug=False) обязателен.
    payment_webhook_hmac_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "PAYMENT_WEBHOOK_HMAC_SECRET",
            "payment_webhook_hmac_secret",
        ),
    )
    cloudpayments_api_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CLOUDPAYMENTS_API_SECRET",
            "cloudpayments_api_secret",
        ),
    )
    kaspi_webhook_hmac_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "KASPI_HMAC_SECRET",
            "kaspi_webhook_hmac_secret",
        ),
    )
    freedom_pay_webhook_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "FREEDOM_PAY_WEBHOOK_SECRET",
            "freedom_pay_webhook_secret",
        ),
    )

    # --- iiko Webhook ---
    iiko_webhook_secret: str = Field(
        default="",
        validation_alias=AliasChoices("IIKO_WEBHOOK_SECRET", "iiko_webhook_secret"),
        description="Секрет для заголовка X-Iiko-Webhook-Secret. Пусто — проверка отключена.",
    )
    iiko_webhook_sync_debounce_sec: int = Field(
        default=30,
        ge=5,
        validation_alias=AliasChoices("IIKO_WEBHOOK_SYNC_DEBOUNCE_SEC", "iiko_webhook_sync_debounce_sec"),
        description="Минимальный интервал между webhook-triggered sync одной орг (дебаунс, сек)",
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
    # Синхронизация меню: только типы Dish / Good / Product (Modifier/Ingredient и т.д. не попадут; False — все типы из API)
    iiko_menu_sync_only_dish_good: bool = Field(
        default=False,
        validation_alias=AliasChoices("IIKO_MENU_SYNC_ONLY_DISH_GOOD", "iiko_menu_sync_only_dish_good"),
    )
    # Авто-импорт номенклатуры из iiko в фоне (филиалы с сохранёнными кредами). 0 = выключено.
    iiko_menu_auto_sync_interval_seconds: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices(
            "IIKO_MENU_AUTO_SYNC_INTERVAL_SECONDS",
            "iiko_menu_auto_sync_interval_seconds",
        ),
        description="Интервал фонового sync_menu для всех филиалов с iiko (сек). Рекомендация prod: 3600–21600.",
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
    # Отдельные legacy-креды супер-админа (опционально).
    # Если заданы — позволяют войти без StaffUser (по env) и получить is_superadmin=true.
    superadmin_username: str = Field(
        default="",
        validation_alias=AliasChoices("SUPERADMIN_USERNAME", "superadmin_username"),
    )
    superadmin_password: str = Field(
        default="",
        validation_alias=AliasChoices("SUPERADMIN_PASSWORD", "superadmin_password"),
    )
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

    # Тариф подписки (₸/мес) для грубой оценки окупаемости на дашборде; 0 — не считать.
    owner_subscription_monthly_kzt: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("OWNER_SUBSCRIPTION_MONTHLY_KZT", "owner_subscription_monthly_kzt"),
    )

    # --- Отзывы после заказа ---
    review_requests_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("REVIEW_REQUESTS_ENABLED", "review_requests_enabled"),
    )
    review_request_delay_sec: int = Field(
        default=1800,
        ge=60,
        validation_alias=AliasChoices("REVIEW_REQUEST_DELAY_SEC", "review_request_delay_sec"),
        description="Задержка перед запросом отзыва после SENT_TO_IIKO/COMPLETED (сек; по умолчанию 30 мин)",
    )
    guestcare_sync_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("GUESTCARE_SYNC_ENABLED", "guestcare_sync_enabled"),
        description="ARQ cron: авто-синхронизация внешних отзывов (2GIS/Google) в external_reviews",
    )
    draft_recovery_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("DRAFT_RECOVERY_ENABLED", "draft_recovery_enabled"),
        description="WA-nudge для DRAFT-заказов старше 45 мин (Money Core)",
    )

    # --- Ночные предзаказы / смена ---
    shift_alert_timeout_min: int = Field(
        default=60,
        ge=5,
        validation_alias=AliasChoices("SHIFT_ALERT_TIMEOUT_MIN", "shift_alert_timeout_min"),
        description="Через сколько минут после открытия алертовать суперадмина, если никто не нажал «на смене»",
    )

    # --- Лояльность ---
    loyalty_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("LOYALTY_ENABLED", "loyalty_enabled"),
    )
    loyalty_points_per_kzt: int = Field(
        default=100,
        ge=1,
        validation_alias=AliasChoices("LOYALTY_POINTS_PER_KZT", "loyalty_points_per_kzt"),
        description="Сколько тенге нужно потратить, чтобы получить 1 балл (по умолчанию 100 KZT = 1 балл)",
    )

    # --- Маркетинговые рассылки ---
    marketing_blast_rate_per_min: int = Field(
        default=60,
        ge=1,
        le=500,
        validation_alias=AliasChoices("MARKETING_BLAST_RATE_PER_MIN", "marketing_blast_rate_per_min"),
        description="Максимум WhatsApp-сообщений в минуту при рассылке",
    )

    # --- PostgreSQL pool (SQLAlchemy) ---
    # 0 / -1 = авто по DATABASE_URL (Supabase session pooler → малый пул).
    db_pool_size: int = Field(
        default=0,
        ge=0,
        le=50,
        validation_alias=AliasChoices("DB_POOL_SIZE", "db_pool_size"),
    )
    db_max_overflow: int = Field(
        default=-1,
        ge=-1,
        le=50,
        validation_alias=AliasChoices("DB_MAX_OVERFLOW", "db_max_overflow"),
    )
    db_pool_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        validation_alias=AliasChoices("DB_POOL_TIMEOUT", "db_pool_timeout"),
    )
    db_pool_recycle: int = Field(
        default=1800,
        ge=60,
        le=86_400,
        validation_alias=AliasChoices("DB_POOL_RECYCLE", "db_pool_recycle"),
    )

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
    # Временный аварийный флаг: разрешить старт без SESSION_SECRET / с дефолтными кредами.
    # Нужен только чтобы "не блокировать деплой", затем выключить и задать нормальные секреты.
    allow_insecure_prod_settings: bool = Field(
        default=False,
        validation_alias=AliasChoices("ALLOW_INSECURE_PROD_SETTINGS", "allow_insecure_prod_settings"),
    )

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
                if not self.allow_insecure_prod_settings:
                    raise ValueError("SESSION_SECRET обязателен в продакшене (задайте длинную случайную строку).")
                logger.warning(
                    "SECURITY WARNING: SESSION_SECRET не задан в prod-like окружении. "
                    "Разрешено только потому, что включён ALLOW_INSECURE_PROD_SETTINGS=true. "
                    "Сессии будут подписываться производным ключом (не для продакшена)."
                )
            if (self.admin_username or "").strip().lower() == "admin" and (self.admin_password or "").strip() == "restomind":
                if not self.allow_insecure_prod_settings:
                    raise ValueError(
                        "ADMIN_USERNAME/ADMIN_PASSWORD должны быть изменены в продакшене (дефолтные небезопасны)."
                    )
                logger.warning(
                    "SECURITY WARNING: Используются дефолтные ADMIN_USERNAME/ADMIN_PASSWORD в prod-like окружении. "
                    "Разрешено только потому, что включён ALLOW_INSECURE_PROD_SETTINGS=true."
                )
        return self

    @property
    def is_prod_like(self) -> bool:
        """True для production/staging: строгие проверки webhook (HMAC, Meta app secret). Не путать с app_debug."""
        e = (self.app_environment or "").strip().lower()
        return e in ("production", "prod", "staging")

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
            # Supabase часто даёт `?sslmode=require` (psql/psycopg). Для asyncpg это невалидный kwargs,
            # поэтому конвертируем в `ssl=true` и выкидываем sslmode.
            try:
                from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

                parsed = urlparse(raw)
                # Поддерживаем схемы вида `postgresql+asyncpg://...` тоже
                if parsed.scheme.startswith("postgres") and parsed.query:
                    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
                    if "sslmode" in q:
                        q.pop("sslmode", None)
                        # TLS для asyncpg задаём явно через `connect_args` (см. `app/db/ssl_context.py`),
                        # поэтому не оставляем `ssl=` в query string — иначе возможны коллизии/разный режим verify.
                        q.pop("ssl", None)
                        raw = urlunparse(
                            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(q), parsed.fragment)
                        )
            except Exception:
                pass
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
