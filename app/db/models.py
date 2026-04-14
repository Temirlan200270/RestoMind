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
    Numeric,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех ORM моделей."""

    pass


class Organization(Base):
    """Ресторан / организация. Фундамент мультитенантности для SaaS."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="Название ресторана")
    iiko_api_login: Mapped[str] = mapped_column(String(255), default="", comment="API-логин iiko")
    iiko_organization_id: Mapped[str] = mapped_column(String(255), default="", comment="UUID организации в iiko")
    whatsapp_phone_number_id: Mapped[str] = mapped_column(String(100), default="", comment="ID номера WhatsApp")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Organization id={self.id} name='{self.name}'>"


class OrderStatus(StrEnum):
    """Жизненный цикл заказа."""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SENT_TO_IIKO = "sent_to_iiko"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class User(Base):
    """Пользователь (клиент ресторана), идентифицируется по номеру телефона."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
        comment="ID организации (для мультитенантности)"
    )
    phone: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True, comment="Номер телефона в формате E.164"
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


class ChatLog(Base):
    """Лог сообщений диалога — хранит каждое сообщение для аналитики и восстановления контекста."""

    __tablename__ = "chat_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
    )
    iiko_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True,
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
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0, comment="Цена в тенге")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, comment="Есть ли в наличии")
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="Ссылка на фото")
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(60), nullable=False, unique=True,
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="Активно ли правило")
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, comment="Приоритет: больше = проверяется раньше",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<PackagingRule id={self.id} kind='{self.kind}' price={self.price}>"


class IntegrationEvent(Base):
    """
    Журнал последних событий синхронизации (меню, стоп-листы) для админки.
    """

    __tablename__ = "integration_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    message_text: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="", comment="Текст последней ошибки")
    attempts: Mapped[int] = mapped_column(Integer, default=3, comment="Сколько попыток было сделано")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, comment="Отмечено как решённое")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<FailedTask id={self.id} phone={self.phone} resolved={self.resolved}>"


class PaymentEvent(Base):
    """
    Аудит оплат: каждое изменение prepayment_status записывается сюда.
    Подготовка к будущим вебхукам от платёжных систем.
    """

    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="prepayment_confirmed | prepayment_waived | webhook_paid | manual_reset",
    )
    actor: Mapped[str] = mapped_column(
        String(100), default="admin", comment="Кто инициировал: admin / webhook / system",
    )
    amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True, comment="Сумма, если релевантно")
    note: Mapped[str] = mapped_column(Text, default="", comment="Комментарий или ID транзакции")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<PaymentEvent id={self.id} order={self.order_id} type={self.event_type}>"


class IntegrationHealth(Base):
    """
    Одна строка (id=1): последние результаты фоновой/ручной синхронизации с iiko.
    Для индикаторов в админке.
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
