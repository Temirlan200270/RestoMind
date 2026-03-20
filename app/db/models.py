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

    # Связи
    user: Mapped["User"] = relationship(back_populates="orders")

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Связи
    user: Mapped["User"] = relationship(back_populates="chat_logs")

    def __repr__(self) -> str:
        return f"<ChatLog id={self.id} role={self.role}>"


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
