"""
SQLAlchemy ORM модели.
Таблицы: users, orders, chat_logs, bookings, menu_items.
"""

from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    Index,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех ORM моделей."""

    pass


class Tenant(Base):
    """Сеть / плательщик (франшиза). Филиалы — ``Organization`` с ``tenant_id``."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="Название сети или холдинга")
    plan: Mapped[str] = mapped_column(
        String(64), default="standard", server_default="standard", comment="Тарифный план (продуктовый)",
    )
    brand_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Кастомное название бренда в шапке админки",
    )
    brand_color_hex: Mapped[str | None] = mapped_column(
        String(9), nullable=True, comment="HEX цвета акцента (#RRGGBB), валидируется на бэкенде",
    )
    brand_logo_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="Публичный URL логотипа (POST /branding/logo заполняет)",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    plan_status: Mapped[str] = mapped_column(
        String(32),
        default="active",
        server_default="active",
        comment="active | suspended — блок входа и WhatsApp для всех филиалов tenant",
    )
    is_network: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
        comment="Phase 1 OS: True = сеть/франшиза — включает Branch Switcher и сетевую аналитику",
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} name='{self.name}'>"


class BillingUsageDaily(Base):
    """Суточный rollup использования AI по tenant (из ai_usage_logs)."""

    __tablename__ = "billing_usage_daily"
    __table_args__ = (UniqueConstraint("tenant_id", "day", name="uq_billing_usage_daily_tenant_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, comment="UTC-календарный день")
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ai_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Organization(Base):
    """Ресторан / организация. Фундамент мультитенантности для SaaS."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tenants.id"), nullable=True, index=True,
        comment="Сеть; NULL — одиночный ресторан до миграции",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="Название ресторана")
    slug: Mapped[str] = mapped_column(
        String(120), default="", server_default="", index=True, comment="URL-safe идентификатор арендатора",
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="Etc/GMT-5",
        server_default="Etc/GMT-5",
        comment="IANA timezone key (например Etc/GMT-5 для UTC+5 или Asia/Almaty)",
    )
    currency: Mapped[str] = mapped_column(String(8), default="KZT", server_default="KZT", comment="ISO код валюты")
    iiko_api_login: Mapped[str] = mapped_column(
        String(255), default="", comment="API-логин iiko (legacy plaintext; предпочтительно iiko_api_login_enc)",
    )
    iiko_api_login_enc: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Fernet-токен (строка) для apiLogin; при наличии имеет приоритет над iiko_api_login",
    )
    iiko_organization_id: Mapped[str] = mapped_column(String(255), default="", comment="UUID организации в iiko")
    iiko_terminal_group_id: Mapped[str] = mapped_column(
        String(255), default="", server_default="", comment="UUID терминальной группы (стоп-лист, deliveries)",
    )
    whatsapp_phone_number_id: Mapped[str] = mapped_column(String(100), default="", comment="ID номера WhatsApp")
    prepayment_enforced: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Ложь — не требовать предоплату по порогу; оператор подтверждает оплату вручную",
    )
    prepayment_legal_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Дополнительный дисклеймер для гостя при предоплате по порогу суммы (редактируется в админке)",
    )
    auto_send_to_iiko_after_payment: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
        comment="После вебхука оплаты: подтвердить заказ и вызвать iiko deliveries/create без ручного шага",
    )
    payment_config_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment=(
            "Per-org конфигурация платёжных провайдеров: "
            "{provider: {enabled, webhook_secret_enc, extra_json}}. "
            "Секреты хранятся в Fernet-зашифрованном виде (APP_SECRETS_FERNET_KEY)."
        ),
    )
    telegram_ops_chat_id: Mapped[str] = mapped_column(
        String(32), default="", server_default="", comment="Telegram chat_id для алертов персоналу (приоритет над глобальным env)",
    )
    schedule_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="График работы по дням недели (структурированный JSON)",
    )
    force_closed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Экстренное закрытие: заведение закрыто до этого момента (UTC)",
    )
    force_closed_reason: Mapped[str] = mapped_column(
        String(255),
        default="",
        server_default="",
        comment="Причина экстренного закрытия (показывается боту)",
    )
    review_url_2gis: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Ссылка на страницу 2GIS для отзывов (отправляется клиенту после 👍)",
    )
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Расширяемые org-настройки без отдельной таблицы; MVP GuestCare External хранит импорт отзывов",
    )
    integration_config_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment=(
            "Per-org интеграции: iiko_office {host, login, password_enc, store_id, department_id}, "
            "другие адаптеры без новых колонок"
        ),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        comment="Демо-организация для гостевого режима",
    )
    max_discount_pct: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
        comment="Decision Engine: максимальный % скидки, который AI может применить (0 = запрещено)",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Organization id={self.id} name='{self.name}'>"


class Location(Base):
    """Под-точка внутри филиала (зал, касса, dark kitchen). Phase 1.1 OS."""

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_locations_org_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), default="main", server_default="main")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Location id={self.id} org={self.organization_id} name='{self.name}'>"


class StaffRole(StrEnum):
    """Роль сотрудника в админке."""

    ADMIN = "admin"
    MANAGER = "manager"
    OPERATOR = "operator"


class StaffUser(Base):
    """Сотрудник ресторана: вход в админку по email (не путать с клиентским User)."""

    __tablename__ = "staff_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    tenant_owner_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Идентификатор сети (tenants.id): все филиалы с organizations.tenant_id = tenant_owner_id доступны в селекторе; NULL — один филиал",
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), default=StaffRole.ADMIN.value, server_default=StaffRole.ADMIN.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superadmin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        comment="Права владельца платформы (доступ к Super Admin)",
    )
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="RBAC: assigned_org_ids для manager; прочие флаги без миграций",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<StaffUser id={self.id} email='{self.email}' org={self.organization_id}>"


class RegistrationRequest(Base):
    """Заявка на подключение ресторана (до ручной модерации)."""

    __tablename__ = "registration_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="Название заведения")
    contact_name: Mapped[str] = mapped_column(String(255), default="", server_default="", comment="Контактное лицо")
    phone: Mapped[str] = mapped_column(String(32), default="", server_default="", comment="Телефон для связи")
    email: Mapped[str] = mapped_column(String(255), default="", server_default="", comment="Email для связи")
    has_iiko: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        comment="Есть ли iiko на стороне клиента",
    )
    note: Mapped[str] = mapped_column(Text, default="", server_default="", comment="Комментарий клиента")
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        server_default="pending",
        index=True,
        comment="pending | approved | rejected",
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_staff_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("staff_users.id"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<RegistrationRequest id={self.id} restaurant='{self.restaurant_name}' status={self.status}>"


class OrderStatus(StrEnum):
    """Жизненный цикл заказа."""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SENDING_TO_IIKO = "sending_to_iiko"
    SENT_TO_IIKO = "sent_to_iiko"
    IN_TRANSIT = "in_transit"
    WAITING_PICKUP = "waiting_pickup"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class User(Base):
    """Пользователь (клиент ресторана), идентифицируется по номеру телефона."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "phone", name="uq_users_organization_phone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
        comment="ID организации (мультитенантность)",
    )
    phone: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True, comment="Номер телефона в формате E.164 (уникален в пределах организации)"
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="Имя клиента")
    operator_note: Mapped[str] = mapped_column(
        Text, default="", server_default="", comment="Внутренняя заметка оператора (аллергии, VIP и т.д.)"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="Активен ли пользователь")
    ai_paused: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="ИИ отключён для этого клиента (персистентно; дублирует смысл HUMAN_MODE)",
    )
    ai_snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="UTC: до этого времени LLM не вызывается (временная пауза без смены Redis state)",
    )
    current_state: Mapped[str] = mapped_column(
        String(50), default="chatting", server_default="chatting",
        comment="Состояние диалога (backup для Redis при eviction)",
    )
    current_pending_order_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="ID заказа, ожидающего подтверждения (backup для Redis)",
    )
    current_pending_booking_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="ID бронирования, ожидающего подтверждения (backup для Redis)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Персистентные флаги клиента (отказы от допродаж upsell_rejections → ISO UTC и др.)",
    )
    marketing_opt_out: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        comment="Клиент отказался от маркетинговых рассылок",
    )

    # Связи
    orders: Mapped[list["Order"]] = relationship(back_populates="user", lazy="selectin")
    chat_logs: Mapped[list["ChatLog"]] = relationship(back_populates="user", lazy="selectin")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="user", lazy="selectin")

    def __repr__(self) -> str:
        return f"<User id={self.id} phone={self.phone}>"


class Order(Base):
    """Заказ клиента. Позиции хранятся в JSONB для гибкости (модификаторы, исключения)."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
    )
    location_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Под-точка филиала; NULL = не привязано к конкретной location",
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), default="draft", nullable=False, index=True,
        comment="Статус заказа: draft / confirmed / sent_to_iiko / completed / cancelled"
    )
    items_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
        comment="Позиции заказа в формате JSON (название, кол-во, модификаторы, исключения)"
    )
    total_price: Mapped[float] = mapped_column(
        Numeric(10, 2), default=0, comment="Итоговая сумма заказа"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    iiko_last_error: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        default=None,
        comment="Текст ошибки при последней попытке отправить заказ в iiko (если не пусто — показать в админке)",
    )
    booking_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("bookings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Бронь при предзаказе в зале (заказ + стол в одной карточке)",
    )
    prepayment_status: Mapped[str] = mapped_column(
        String(30),
        default="not_required",
        server_default="not_required",
        comment="not_required | pending | paid | waived — предоплата за бронь (Kaspi/эквайринг)",
    )
    payment_link_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="Ссылка на оплату; заполняет оператор или платёжный webhook",
    )
    payment_provider: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="kaspi | manual | generic — источник подтверждения оплаты",
    )
    external_payment_id: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        index=True,
        comment="Внешний id транзакции (идемпотентность вебхука + сверка)",
    )
    payment_amount_captured: Mapped[float | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Сумма, зафиксированная при оплате (вебхук или ручное подтверждение)",
    )
    kind: Mapped[str] = mapped_column(
        String(30),
        default="regular",
        server_default="regular",
        comment="regular | night_preorder",
    )
    # Optimistic locking: SQLAlchemy увеличивает при UPDATE; при конфликте — StaleDataError
    row_version: Mapped[int] = mapped_column(
        "version",
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="Версия строки заказа для защиты от гонок при merge/правках",
    )

    @property
    def version(self) -> int:
        """Алиас к row_version (колонка в БД — version, optimistic locking)."""
        return int(self.row_version)

    # Связи
    user: Mapped["User"] = relationship(back_populates="orders")
    booking: Mapped["Booking | None"] = relationship(back_populates="linked_order")

    def __repr__(self) -> str:
        return f"<Order id={self.id} status={self.status} total={self.total_price}>"


class RecommendationEvent(Base):
    """События допродажи по заказу (SQL-агрегаты ROI); синхронизируются из order_meta."""

    __tablename__ = "recommendation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    item_iiko_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    item_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<RecommendationEvent order={self.order_id} accepted={self.accepted}>"


class ChatLog(Base):
    """Лог сообщений диалога — хранит каждое сообщение для аналитики и восстановления контекста."""

    __tablename__ = "chat_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
        comment="Денормализация для фильтров админки",
    )
    location_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="Роль отправителя: user / assistant / system"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="Текст сообщения")
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Служебные данные для админки (интент, уверенность, internal monologue)",
    )
    provider_message_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
        comment="ID исходящего сообщения WhatsApp (wamid) из ответа Graph API / вебхука statuses",
    )
    delivery_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="sending | sent | delivered | read | failed — только исходящие в WhatsApp",
    )
    error_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Ответ Meta при ошибке доставки (failed)",
    )
    status_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Время последнего изменения delivery_status",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Связи
    user: Mapped["User"] = relationship(back_populates="chat_logs")

    def __repr__(self) -> str:
        return f"<ChatLog id={self.id} role={self.role}>"


class WhatsappInboundDedupe(Base):
    """
    Идемпотентность входящих сообщений WhatsApp по message_id от Meta.
    Дополняет Redis TTL-дедуп: переживает сброс кэша и даёт аудит.
    """

    __tablename__ = "whatsapp_inbound_dedupe"

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="id сообщения из вебхука Meta")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="processing",
        server_default="processing",
        comment="processing | done | failed",
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<WhatsappInboundDedupe message_id={self.message_id!r}>"


class Booking(Base):
    """Бронирование столика."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
    )
    location_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    booking_date: Mapped[date] = mapped_column(Date, nullable=False, comment="Дата бронирования")
    booking_time: Mapped[time] = mapped_column(Time, nullable=False, comment="Время бронирования")
    guests: Mapped[int] = mapped_column(Integer, default=2, comment="Количество гостей")
    hall: Mapped[str] = mapped_column(
        String(20),
        default="hall_1",
        server_default="hall_1",
        comment="Зал: hall_1 | hall_2 | vip (VIP — один стол на слот)",
    )
    comment: Mapped[str] = mapped_column(
        Text, default="", comment="Пожелания клиента (у окна, детский стул и т.д.)"
    )
    status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False, index=True,
        comment="Статус: pending / confirmed / cancelled"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Связи
    user: Mapped["User"] = relationship(back_populates="bookings")
    linked_order: Mapped["Order | None"] = relationship(back_populates="booking", uselist=False)

    def __repr__(self) -> str:
        return f"<Booking id={self.id} date={self.booking_date} time={self.booking_time} guests={self.guests}>"


class MenuItem(Base):
    """
    Позиция меню ресторана.
    Синхронизируется из iiko (или заполняется вручную в MVP).
    """

    __tablename__ = "menu_items"
    __table_args__ = (
        # Multi-tenant: один и тот же UUID iiko может существовать у разных организаций.
        # Уникальность должна быть на пару (organization_id, iiko_id).
        UniqueConstraint("organization_id", "iiko_id", name="uq_menu_items_org_iiko_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
    )
    iiko_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True,
        comment="ID продукта в iiko (UUID)"
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True, comment="Название блюда")
    category: Mapped[str] = mapped_column(String(100), default="", comment="Категория (Пицца, Супы, Напитки...)")
    description: Mapped[str] = mapped_column(Text, default="", comment="Описание блюда")
    tags: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default="",
        comment="Теги сочетаемости для ИИ: через запятую (напр. хит, к нему: ачичук, чай)",
    )
    portion_kind: Mapped[str] = mapped_column(
        String(20),
        default="single",
        server_default="single",
        comment="single — порционное; shareable — на компанию / кушать вместе",
    )
    serves_min: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", comment="Мин. число гостей, на которых рассчитана позиция",
    )
    serves_max: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", comment="Макс. число гостей для комфортной порции",
    )
    allergens: Mapped[str] = mapped_column(
        Text, default="", server_default="", comment="Аллергены и риски (текст для ИИ и оператора)",
    )
    ingredients_summary: Mapped[str] = mapped_column(
        Text, default="", server_default="", comment="Краткий состав для подсказок и ограничений",
    )
    dietary_tags: Mapped[str] = mapped_column(
        Text, default="", server_default="", comment="Теги диеты: веган, халяль, безглютеновый — через запятую",
    )
    upsell_pairs: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default="",
        comment="Пары допродаж: iiko UUID или названия через запятую, как в меню",
    )
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0, comment="Цена в тенге")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, comment="Есть ли в наличии")
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="Ссылка на фото")
    embedding: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        comment="float32 LE vector bytes для семантического top-k (E12)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<MenuItem id={self.id} name='{self.name}' price={self.price}>"


class KnowledgeItem(Base):
    """
    База знаний для AI: парковка, банкеты, часы, политики.
    Активные записи подмешиваются в system prompt (см. knowledge_context).
    """

    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
        comment="NULL — общие правила для всех; иначе только для выбранной организации",
    )
    knowledge_kind: Mapped[str] = mapped_column(
        String(32),
        default="facility",
        server_default="facility",
        comment="facility — справочник заведения; persona — характер бота",
    )
    category: Mapped[str] = mapped_column(
        String(120), default="", comment="Группа: Парковка, Банкеты, Общее…",
    )
    question: Mapped[str] = mapped_column(String(500), nullable=False, comment="Краткий заголовок / формулировка вопроса")
    answer: Mapped[str] = mapped_column(Text, nullable=False, comment="Текст ответа (можно несколько абзацев)")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="Участвует ли в контексте для LLM")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="Порядок вывода в справочнике")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeItem id={self.id} category='{self.category[:20]}'>"


class PackagingRule(Base):
    """
    Правило упаковки: связь «тип блюда → контейнер → цена».
    Вместо хардкода в .env: правится в админке без деплоя.
    """

    __tablename__ = "packaging_rules"
    __table_args__ = (
        UniqueConstraint("organization_id", "kind", name="uq_packaging_rules_org_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(60), nullable=False,
        comment="Ключ правила: manty, shashlik, plov_half, plov_1kg_tabak, plov_1kg_foil, fries, standard, delivery",
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="Отображаемое название контейнера/услуги")
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0, comment="Цена за единицу, ₸")
    iiko_product_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="UUID номенклатуры iiko для строки заказа",
    )
    keywords: Mapped[str] = mapped_column(
        Text, default="", server_default="",
        comment="Ключевые слова для авто-сопоставления: 'мант,манты' (запятая=ИЛИ, + =И). Пусто=дефолт.",
    )
    option_key: Mapped[str] = mapped_column(
        String(60), default="", server_default="",
        comment="Для блюд с выбором упаковки: tabak / foil_kazan. Пусто — без выбора.",
    )
    scope: Mapped[str] = mapped_column(String(20), default="item", server_default="item")
    category_match: Mapped[str] = mapped_column(String(120), default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="Активно ли правило")
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, comment="Приоритет: больше = проверяется раньше",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<PackagingRule id={self.id} kind='{self.kind}' price={self.price}>"


class UpsellRule(Base):
    """Детерминированные правила допродажи (Strategy Engine v1)."""

    __tablename__ = "upsell_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    trigger_mode: Mapped[str] = mapped_column(
        String(32), default="missing_category", server_default="missing_category",
        comment="missing_category: в корзине нет строки с категорией trigger_category",
    )
    trigger_category: Mapped[str] = mapped_column(
        String(120), default="", comment="Подстрока категории меню (без учёта регистра)",
    )
    suggest_category: Mapped[str] = mapped_column(
        String(120), default="", comment="Категория кандидата для предложения",
    )
    min_order_sum: Mapped[float] = mapped_column(
        Numeric(12, 2), default=0, comment="Минимальная сумма заказа (₸) для срабатывания",
    )
    max_order_sum: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="Верхняя граница суммы; NULL = без ограничения",
    )
    phrase_template: Mapped[str] = mapped_column(
        Text,
        default="К заказу отлично подойдёт {item_name} ({price} ₸). Добавить?",
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<UpsellRule id={self.id} org={self.organization_id}>"


class IntegrationEvent(Base):
    """
    Журнал последних событий синхронизации (меню, стоп-листы) для админки.
    """

    __tablename__ = "integration_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, comment="menu_sync | stoplist_sync | …")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str] = mapped_column(Text, default="", comment="Краткий итог или текст ошибки")


class EscalationEvent(Base):
    """
    События эскалации на оператора (intent escalate → HUMAN_MODE).
    Для аналитики «сколько раз бот звал на помощь».
    """

    __tablename__ = "escalation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    user_message: Mapped[str] = mapped_column(Text, default="", comment="Последнее сообщение клиента")
    reason: Mapped[str] = mapped_column(Text, default="", comment="Текст ответа бота / контекст")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )


class FailedTask(Base):
    """
    Сообщения, которые не удалось обработать после нескольких попыток.
    Для ручного retry или диагностики в админке.
    """

    __tablename__ = "failed_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    message_text: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="", comment="Текст последней ошибки")
    attempts: Mapped[int] = mapped_column(Integer, default=3, comment="Сколько попыток было сделано")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, comment="Отмечено как решённое")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<FailedTask id={self.id} phone={self.phone} resolved={self.resolved}>"


class PaymentWebhookEvent(Base):
    """
    Сырые входящие HTTP запросы платёжного webhook (аудит до/после проверки подписи).
    """

    __tablename__ = "payment_webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
    )
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    provider_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_payment_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    signature_header: Mapped[str | None] = mapped_column(String(512), nullable=True)
    http_headers_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    payload_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    payload_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    parsed_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payment_event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("payment_events.id"), nullable=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )

    def __repr__(self) -> str:
        return f"<PaymentWebhookEvent id={self.id} provider={self.provider_slug} verified={self.verified}>"


class PaymentEvent(Base):
    """
    Аудит оплат: каждое изменение prepayment_status записывается сюда.
    Подготовка к будущим вебхукам от платёжных систем.
    """

    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("order_id", "event_type", "note", name="uq_payment_event_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="prepayment_confirmed | prepayment_waived | webhook_paid | webhook_failed | manual_reset",
    )
    actor: Mapped[str] = mapped_column(
        String(100), default="admin", comment="Кто инициировал: admin / webhook / system",
    )
    amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True, comment="Сумма, если релевантно")
    note: Mapped[str] = mapped_column(Text, default="", comment="Комментарий или ID транзакции")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<PaymentEvent id={self.id} order={self.order_id} type={self.event_type}>"


class PaymentTxStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    WAIVED = "waived"


class PaymentTransaction(Base):
    """
    Транзакция оплаты — source of truth для платёжного flow.
    Один заказ может иметь несколько транзакций (retry, re-initiation после expired).
    Order.prepayment_status — совместимый alias от последней транзакции.
    """

    __tablename__ = "payment_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_tx_idempotency_key"),
        Index("ix_payment_tx_order_id", "order_id"),
        Index("ix_payment_tx_org_status", "organization_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )

    provider: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="freedom_pay | kaspi | cloudpayments | manual | generic",
    )
    provider_payment_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True,
        comment="ID транзакции в системе провайдера (заполняется после webhook или initiation)",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PaymentTxStatus.PENDING.value,
        server_default=PaymentTxStatus.PENDING.value,
        index=True,
        comment="pending | paid | failed | expired | waived",
    )

    amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
        comment="Сумма к оплате (в единицах currency)",
    )
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="KZT", server_default="KZT",
    )

    payment_url: Mapped[str | None] = mapped_column(
        String(1024), nullable=True,
        comment="Ссылка/QR для клиента; заполняется при initiation",
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(200), nullable=False,
        comment="Ключ идемпотентности: re-initiation создаёт новую запись с новым ключом",
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Когда ссылка протухает (провайдер или наш таймаут)",
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Время подтверждения оплаты (webhook от провайдера)",
    )

    failure_reason: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
        comment="Текст ошибки от провайдера при status=failed",
    )

    provider_payload_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
        comment="Сырой ответ провайдера при initiation или webhook (без sensitive данных)",
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
        comment="Внутренние метаданные: description, retry_count, etc.",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<PaymentTransaction id={self.id} order={self.order_id} provider={self.provider} status={self.status}>"


class OrganizationPaymentConfig(Base):
    """
    Платёжные credentials на уровне организации (multi-tenant SaaS).
    Секреты хранятся зашифрованными через APP_SECRETS_FERNET_KEY.
    Один провайдер — одна запись. Флаг is_primary = единственный активный по умолчанию.
    """

    __tablename__ = "organization_payment_configs"
    __table_args__ = (
        UniqueConstraint("organization_id", "provider", name="uq_org_payment_config_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="freedom_pay | kaspi | cloudpayments",
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="Основной провайдер для автоматической генерации ссылок",
    )

    merchant_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
        comment="Публичный идентификатор мерчанта у провайдера (не секрет)",
    )
    encrypted_api_key: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Fernet-зашифрованный API ключ провайдера",
    )
    encrypted_secret_key: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Fernet-зашифрованный секрет (HMAC-ключ для webhook и initiation)",
    )
    encrypted_public_key: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Fernet-зашифрованный публичный ключ (если требует провайдер)",
    )

    extra_config_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
        comment="Доп. настройки провайдера: callback_url, environment (test/prod) и др.",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<OrganizationPaymentConfig id={self.id} org={self.organization_id} provider={self.provider}>"


class SystemEvent(Base):
    """Durable domain event stream for analytics, AI intelligence, audit, and future automation."""

    __tablename__ = "system_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_system_event_idempotency_key"),
        Index("ix_system_events_org_created", "organization_id", "created_at"),
        Index("ix_system_events_org_type_created", "organization_id", "event_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="app", server_default="app")
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<SystemEvent id={self.id} org={self.organization_id} type={self.event_type}>"


class OperationalInsight(Base):
    """Generated restaurant intelligence insight shown in the admin panel."""

    __tablename__ = "operational_insights"
    __table_args__ = (
        Index("ix_operational_insights_org_status_created", "organization_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    insight_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info", server_default="info")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", server_default="new", index=True)
    was_useful: Mapped[bool | None] = mapped_column(Boolean, nullable=True, comment="Оператор отметил инсайт полезным/бесполезным")
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="Заметка оператора при закрытии инсайта")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<OperationalInsight id={self.id} org={self.organization_id} severity={self.severity}>"


class ExternalReview(Base):
    """Imported external review for GuestCare workflows."""

    __tablename__ = "external_reviews"
    __table_args__ = (
        UniqueConstraint("organization_id", "source", "external_id", name="uq_external_reviews_org_source_id"),
        Index("ix_external_reviews_org_imported", "organization_id", "imported_at"),
        Index("ix_external_reviews_org_status", "organization_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="external", server_default="external")
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str] = mapped_column(String(160), nullable=False, default="", server_default="")
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new", server_default="new", index=True)
    reply_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<ExternalReview id={self.id} org={self.organization_id} source={self.source}>"


class InventoryStockSnapshot(Base):
    """Latest known stock level for SupplyMind/OS alerts."""

    __tablename__ = "inventory_stock_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "location_id",
            "source",
            "sku",
            name="uq_inventory_stock_org_location_source_sku",
        ),
        Index("ix_inventory_stock_org_updated", "organization_id", "updated_at"),
        Index("ix_inventory_stock_org_location", "organization_id", "location_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    location_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual", server_default="manual")
    sku: Mapped[str] = mapped_column(String(120), nullable=False)
    ingredient: Mapped[str] = mapped_column(String(240), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    quantity: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False, default=0, server_default="0")
    min_quantity: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)
    reorder_quantity: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)
    daily_usage_estimate: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<InventoryStockSnapshot id={self.id} org={self.organization_id} sku={self.sku}>"


class SupplyPurchaseDraft(Base):
    """Draft purchase order generated from low-stock alerts."""

    __tablename__ = "supply_purchase_drafts"
    __table_args__ = (
        Index("ix_supply_purchase_drafts_org_status", "organization_id", "status"),
        Index("ix_supply_purchase_drafts_org_created", "organization_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    location_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="supplymind", server_default="supplymind")
    title: Mapped[str] = mapped_column(String(240), nullable=False, default="", server_default="")
    items_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<SupplyPurchaseDraft id={self.id} org={self.organization_id} status={self.status}>"


class StaffOnboardingSession(Base):
    """WhatsApp-ready onboarding session for a staff member."""

    __tablename__ = "staff_onboarding_sessions"
    __table_args__ = (
        Index("ix_staff_onboarding_org_status", "organization_id", "status"),
        Index("ix_staff_onboarding_org_phone", "organization_id", "phone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    staff_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    role: Mapped[str] = mapped_column(String(80), nullable=False, default="staff", server_default="staff")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    progress_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<StaffOnboardingSession id={self.id} org={self.organization_id} status={self.status}>"


class VoiceCallLog(Base):
    """Operational log for voice AI calls."""

    __tablename__ = "voice_call_logs"
    __table_args__ = (
        Index("ix_voice_call_logs_org_created", "organization_id", "created_at"),
        Index("ix_voice_call_logs_call_sid", "call_sid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    call_sid: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="twilio", server_default="twilio")
    mode: Mapped[str] = mapped_column(String(40), nullable=False, default="stt_fallback", server_default="stt_fallback")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started", server_default="started")
    transcript: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<VoiceCallLog id={self.id} org={self.organization_id} status={self.status}>"


class RestaurantStateSnapshot(Base):
    """Point-in-time operational state used by the Digital Twin and simulations."""

    __tablename__ = "restaurant_state_snapshots"
    __table_args__ = (
        Index("ix_restaurant_state_snapshots_org_created", "organization_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    active_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    draft_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    confirmed_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cancelled_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    revenue_today: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    avg_check_today: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    queue_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    operator_load: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False, default=0, server_default="0")
    kitchen_load: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False, default=0, server_default="0")
    stoplist_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<RestaurantStateSnapshot id={self.id} org={self.organization_id}>"


class IntelligenceConversation(Base):
    """Saved owner/manager Q&A thread for follow-up intelligence questions."""

    __tablename__ = "intelligence_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntelligenceMessage(Base):
    """Saved intelligence message for audit and later follow-up context."""

    __tablename__ = "intelligence_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("intelligence_conversations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class IntegrationHealth(Base):
    """
    Одна строка (id=1): глобальный снимок (ошибки фона без org, legacy).
    Статус по филиалу см. OrganizationIntegrationSync.
    """

    __tablename__ = "integration_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_stoplist_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_stoplist_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_stoplist_error: Mapped[str] = mapped_column(Text, default="")
    last_menu_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_menu_sync_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_menu_sync_error: Mapped[str] = mapped_column(Text, default="")


class AiUsageLog(Base):
    """Агрегированный учёт токенов AI по организации/дню (upsert: один ряд = org + day)."""

    __tablename__ = "ai_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, comment="Дата UTC агрегации")
    provider: Mapped[str] = mapped_column(String(32), default="openai", server_default="openai")
    model: Mapped[str] = mapped_column(String(64), default="", server_default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    call_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0",
                                              comment="AI-провайдер вернул transient ошибку (сумма за день)")
    p95_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True,
                                                        comment="95-й перцентиль задержки LLM за день (ms)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PipelineLatencyLog(Base):
    """Запись задержек обработки входящего сообщения по стадиям (ms). Одна строка = один WhatsApp-запрос."""

    __tablename__ = "pipeline_latency_logs"
    __table_args__ = (
        Index("ix_pipeline_latency_logs_org_created", "organization_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    pipeline_type: Mapped[str] = mapped_column(
        String(32), default="whatsapp_text", server_default="whatsapp_text",
        comment="whatsapp_text | whatsapp_voice",
    )
    dedupe_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    route_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reply_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class MessageAccountingLog(Base):
    """Агрегированный учёт сообщений WhatsApp по org/дню/направлению/источнику/типу (upsert)."""

    __tablename__ = "message_accounting_logs"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "day", "direction", "source", "message_type",
            name="uq_msg_acct_org_day_dir_src_type",
        ),
        Index("ix_msg_acct_org_day", "organization_id", "day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, comment="UTC-дата агрегации")
    direction: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="inbound | outbound",
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="user | ai | operator | system",
    )
    message_type: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="text | voice | interactive | template",
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BusinessRecommendation(Base):
    """AI-сгенерированная бизнес-рекомендация для ресторана."""

    __tablename__ = "business_recommendations"
    __table_args__ = (
        Index("ix_business_recommendations_org_created", "organization_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    recommendation_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="product_boost | pricing_adj | geo_expansion | upsell_pair | stoplist_impact",
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    confidence_pct: Mapped[int] = mapped_column(Integer, default=50, server_default="50",
                                                 comment="0-100 уверенность на основе sample_count")
    expected_impact_kzt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="new", server_default="new", index=True,
        comment="new | viewed | acted_on | dismissed",
    )
    data_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<BusinessRecommendation id={self.id} org={self.organization_id} type={self.recommendation_type}>"


class CustomerFeedback(Base):
    """Отзыв клиента после завершения заказа (👍 / 👎)."""

    __tablename__ = "customer_feedback"
    __table_args__ = (
        Index("ix_customer_feedback_org_created", "organization_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True,
    )
    rating: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="positive | negative",
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketingBlast(Base):
    """Кампания-рассылка по клиентам через WhatsApp."""

    __tablename__ = "marketing_blasts"
    __table_args__ = (
        Index("ix_marketing_blasts_org_status", "organization_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    segment_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="inactive_30d | frequent | all_active | custom",
    )
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    template_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="draft", server_default="draft",
        comment="draft | sending | done | cancelled",
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_recipients: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    recipients: Mapped[list["MarketingBlastRecipient"]] = relationship(
        back_populates="blast", lazy="dynamic",
    )


class MarketingBlastRecipient(Base):
    """Получатель маркетинговой рассылки."""

    __tablename__ = "marketing_blast_recipients"
    __table_args__ = (
        Index("ix_mbr_blast_status", "blast_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    blast_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("marketing_blasts.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending",
        comment="pending | sent | failed | opted_out",
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    blast: Mapped["MarketingBlast"] = relationship(back_populates="recipients")


class LoyaltyBalance(Base):
    """Текущий бонусный баланс клиента."""

    __tablename__ = "loyalty_balance"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_loyalty_balance_org_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    balance_points: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class LoyaltyTransaction(Base):
    """История начислений/списаний бонусных баллов."""

    __tablename__ = "loyalty_transactions"
    __table_args__ = (
        Index("ix_loyalty_tx_org_user_created", "organization_id", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True,
    )
    points: Mapped[int] = mapped_column(
        Integer, nullable=False, comment=">0 начисление, <0 списание",
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="earn | redeem | adjust",
    )
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrganizationIntegrationSync(Base):
    """Последний результат синхронизации меню и стоп-листа iiko по филиалу (organizations.id)."""

    __tablename__ = "organization_integration_sync"

    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_stoplist_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_stoplist_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_stoplist_error: Mapped[str] = mapped_column(Text, default="")
    last_menu_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_menu_sync_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_menu_sync_error: Mapped[str] = mapped_column(Text, default="")
    last_inventory_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_inventory_sync_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_inventory_sync_error: Mapped[str] = mapped_column(Text, default="")



class AIContextSnapshot(Base):
    """Снимок AI-контекста в момент LLM-вызова — для replay, аудита и отладки решений бота."""

    __tablename__ = "ai_context_snapshots"
    __table_args__ = (
        Index("ix_ai_ctx_snapshots_org_created", "organization_id", "created_at"),
        Index("ix_ai_ctx_snapshots_org_phone", "organization_id", "phone"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    business_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    customer_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    event_slice: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<AIContextSnapshot id={self.id} org={self.organization_id} phone={self.phone}>"


class DailyOrgStats(Base):
    """Дневные агрегаты бизнес-событий по организации. Заполняется analytics_consumer (Phase 2.3).

    Широкая таблица: один ряд = org + day, все метрики как колонки.
    Первичный ключ (organization_id, day) — upsert через ON CONFLICT.
    """

    __tablename__ = "daily_org_stats"
    __table_args__ = (
        Index("ix_daily_org_stats_org_day", "organization_id", "day"),
    )

    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, primary_key=True,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, primary_key=True)
    orders_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    orders_confirmed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    orders_cancelled: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bookings_confirmed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bookings_cancelled: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bookings_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    payments_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    payments_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    revenue_kzt: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    escalations: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    operator_takeovers: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ai_messages_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dialogs_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<DailyOrgStats org={self.organization_id} day={self.day}>"


class AuditLog(Base):
    """Иммутабельный аудит-лог бизнес-событий (Phase 5 OS).

    Пишется audit_consumer при каждом emit_event().
    Запрещено редактировать/удалять (immutable append-only).
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_org_created", "organization_id", "created_at"),
        Index("ix_audit_log_org_action", "organization_id", "action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    actor: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} org={self.organization_id} action={self.action}>"


class SuperadminAuditLog(Base):
    """Иммутабельный аудит действий Super Admin (платформенный уровень)."""

    __tablename__ = "superadmin_audit_log"
    __table_args__ = (
        Index("ix_superadmin_audit_created", "created_at"),
        Index("ix_superadmin_audit_org_created", "organization_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_staff_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("staff_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    organization_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<SuperadminAuditLog id={self.id} action={self.action} target={self.target_type}:{self.target_id}>"
