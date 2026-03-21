"""
Pydantic-схемы для структурированных ответов AI (Gemini).
Используются как response_json_schema для гарантированного Structured Output.
"""

from typing import Literal

from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    """Одна позиция заказа, распознанная ИИ из речи клиента."""

    name: str = Field(
        ..., description="Название блюда или напитка"
    )
    iiko_item_id: str = Field(
        default="",
        description="UUID продукта из iiko (из контекста меню, поле [id: ...]). Если ID неизвестен — оставить пустым.",
    )
    quantity: int = Field(
        default=1, ge=1, description="Количество порций"
    )
    modifiers_ids: list[str] = Field(
        default_factory=list,
        description="UUID модификаторов из iiko (дополнительные опции: соус, размер и т.д.)",
    )
    exclude_ingredients: list[str] = Field(
        default_factory=list,
        description="Ингредиенты, которые клиент просит убрать (например: 'без лука', 'без сыра')",
    )


class BookingDetails(BaseModel):
    """Детали бронирования столика, извлечённые из диалога."""

    date: str = Field(
        ..., description="Дата бронирования в формате YYYY-MM-DD"
    )
    time: str = Field(
        ..., description="Время бронирования в формате HH:MM"
    )
    guests: int = Field(
        default=2, ge=1, le=50, description="Количество гостей"
    )
    hall: Literal["hall_1", "hall_2", "vip"] = Field(
        default="hall_1",
        description=(
            "Зал: hall_1 — Зал 1, hall_2 — Зал 2, vip — VIP зал "
            "(в ресторане один VIP-стол на слот; если занят — предложи другое время или другой зал)"
        ),
    )
    comment: str = Field(
        default="", description="Дополнительные пожелания клиента (у окна, детский стул и т.д.)"
    )


class AIBrainResponse(BaseModel):
    """
    Структурированный ответ от ИИ.
    Определяет намерение (intent) клиента и содержит данные для обработки.
    """

    intent: Literal["order", "book", "faq", "escalate"] = Field(
        ...,
        description=(
            "Намерение клиента: "
            "'order' — хочет сделать/изменить заказ, "
            "'book' — забронировать столик, "
            "'faq' — задаёт вопрос (меню, часы работы, адрес), "
            "'escalate' — нужен живой оператор"
        ),
    )
    reply_text: str = Field(
        ..., description="Текст ответа клиенту на русском языке"
    )
    items: list[OrderItem] = Field(
        default_factory=list,
        description="Список позиций заказа (заполняется только при intent='order')",
    )
    booking_details: BookingDetails | None = Field(
        default=None,
        description="Детали бронирования (заполняется только при intent='book')",
    )
