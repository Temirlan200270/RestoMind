"""
Pydantic-схемы для структурированных ответов AI (Gemini).
Используются как response_json_schema для гарантированного Structured Output.
"""

from typing import Literal

from pydantic import BaseModel, Field

# Язык ответа клиенту (для мультиязычности и выбора голоса TTS)
DetectedReplyLanguage = Literal["ru", "kk", "en", "uz"]


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
    Структурированный ответ от ИИ (Gemini Structured Output).

    Соответствие формулировкам ТЗ (одна схема, без дублирования полей):
    тип заказа и оплата — order_type, payment_method;
    дата/время/гости брони — booking_details (или intent book без блюд);
    предзаказ блюд — is_preorder;
    время визита — booking_time и/или booking_details при предзаказе в зале.
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
        ...,
        description=(
            "Текст ответа клиенту **на том же языке, на котором пишет клиент** "
            "(русский, қазақша, English, o'zbek и смешанный код-свитчинг — естественно и вежливо)."
        ),
    )
    detected_language: DetectedReplyLanguage = Field(
        default="ru",
        description=(
            "Основной язык поля `reply_text`: ru — русский, kk — қазақша, en — English, uz — o'zbek. "
            "Обязан совпадать с языком ответа. Если ответ преимущественно на киргизском или другом языке "
            "вне списка — укажи ru (озвучка по умолчанию)."
        ),
    )
    items: list[OrderItem] = Field(
        default_factory=list,
        description="Список позиций заказа (заполняется только при intent='order')",
    )
    booking_details: BookingDetails | None = Field(
        default=None,
        description=(
            "Детали бронирования: при intent='book' — всегда при наличии даты/времени; "
            "при intent='order' и order_type='hall' и is_preorder=true — обязательно "
            "(дата YYYY-MM-DD, время HH:MM, гости, зал) для связки «бронь + предзаказ блюд»"
        ),
    )
    # --- Логистика и оплата (v2.0) — для intent='order' ---
    order_type: Literal["delivery", "pickup", "hall"] = Field(
        default="delivery",
        description=(
            "Как клиент получит заказ: delivery — доставка (нужен адрес), "
            "pickup — самовывоз (время получения), "
            "hall — в зале ресторана (может сочетаться с бронью / предзаказом)"
        ),
    )
    payment_method: Literal["cash", "card", "remote"] = Field(
        default="cash",
        description="Способ оплаты: cash — наличные, card — карта при получении, remote — перевод/ссылка",
    )
    is_preorder: bool = Field(
        default=False,
        description="Предзаказ блюд к визиту (актуально для зала)",
    )
    booking_time: str | None = Field(
        default=None,
        description="Время визита или самовывоза (свободный текст или HH:MM), если уже известно",
    )
    delivery_address: str = Field(
        default="",
        description="Адрес доставки при order_type=delivery",
    )
    pickup_time_note: str = Field(
        default="",
        description="Когда забрать самовывоз или уточнение времени",
    )
    recognized_speech: str | None = Field(
        default=None,
        description=(
            "Только при голосовом вводе: дословная расшифровка речи клиента. "
            "В обычном текстовом чате — null (не заполнять)."
        ),
    )
