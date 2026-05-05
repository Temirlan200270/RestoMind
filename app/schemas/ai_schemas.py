"""
Pydantic-схемы для структурированных ответов AI (OpenAI structured output / parse).
Используются как response_format для гарантированной схемы AIBrainResponse.
"""

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Язык ответа клиенту (для мультиязычности и выбора голоса TTS)
DetectedReplyLanguage = Literal["ru", "kk", "en", "uz"]


PackagingPlov1Kg = Literal["", "tabak", "foil_kazan"]


class OrderItem(BaseModel):
    """Одна позиция заказа, распознанная ИИ из речи клиента."""

    name: str = Field(
        ..., description="Название блюда или напитка"
    )
    iiko_item_id: str = Field(
        default="",
        description="UUID продукта из iiko (из контекста меню, поле [id: ...]). Если ID неизвестен — оставить пустым.",
    )
    packaging_plov_1kg: PackagingPlov1Kg = Field(
        default="",
        description=(
            "Только для позиции «плов 1 кг»: tabak — контейнер-табак (800 ₸), "
            "foil_kazan — фольгированный казан (650 ₸). Для остальных блюд — пустая строка. "
            "Пока клиент не выбрал — оставь пустым и спроси в reply_text."
        ),
    )
    quantity: int = Field(
        default=1, ge=1, description="Количество порций"
    )
    modifiers_ids: list[str] = Field(
        default_factory=list,
        description="UUID модификаторов из iiko (дополнительные опции: соус, размер и т.д.)",
    )
    modifiers: list[dict[str, Any]] = Field(default_factory=list)
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


class PaymentSplit(BaseModel):
    """Суммы по способам при смешанной оплате (все в тенге, неотрицательные)."""

    cash: float = Field(default=0.0, ge=0, description="Наличными при получении")
    card: float = Field(default=0.0, ge=0, description="Картой при получении")
    remote: float = Field(default=0.0, ge=0, description="Удалённо: Каспи / перевод / ссылка")


OrderActionKind = Literal["add", "remove", "set_quantity"]


class OrderAction(BaseModel):
    """
    Одна дельта к корзине (Phase 18).
    item_id — UUID из контекста меню [id: …] или каноничное название блюда, как в меню.
    """

    action_id: str = Field(
        default="",
        max_length=128,
        description=(
            "Необязательный устойчивый id дельты (uuid или короткий ключ). "
            "Повтор того же id на том же черновике игнорируется. Пусто — сервер вычислит fingerprint."
        ),
    )
    item_id: str = Field(
        ...,
        min_length=1,
        description="ID блюда из контекста меню (UUID iiko) или точное/узнаваемое название позиции",
    )
    action: OrderActionKind = Field(
        ...,
        description=(
            "add — прибавить quantity к текущему количеству; "
            "remove — вычесть quantity (до 0 — строка удаляется); "
            "set_quantity — жёстко установить количество (0 — удалить строку)"
        ),
    )
    quantity: int = Field(default=1, ge=0, description="Количество порций для операции")
    reasoning: str | None = Field(
        default=None,
        description="Краткое обоснование, если действие связано с рекомендацией бота",
    )

    @field_validator("action_id", mode="before")
    @classmethod
    def _strip_action_id(cls, v: object) -> object:
        if v is None:
            return ""
        if isinstance(v, str):
            return v.strip()[:128]
        return v

    @field_validator("item_id", mode="before")
    @classmethod
    def _strip_item_id(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            if not s:
                raise ValueError("item_id не может быть пустым")
            return s
        return v


class AIBrainResponse(BaseModel):
    """
    Структурированный ответ от ИИ (OpenAI structured output по этой схеме).

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
        description=(
            "Полный список позиций заказа при intent='order'. "
            "Если order_actions не пуст — бэкенд мержит дельты с DRAFT; "
            "пустой order_actions сохраняет прежнюю семантику «весь заказ из items»."
        ),
    )
    order_actions: list[OrderAction] = Field(
        default_factory=list,
        description="Инкрементальные правки корзины (add/remove/set_quantity); см. Phase 18",
    )
    is_recommendation: bool = Field(
        default=False,
        description=(
            "True, если в этом ответе бот сам предложил доп. позицию (совет/допродажа), а не только подтвердил заказ"
        ),
    )
    upsell_offered: str | None = Field(
        default=None,
        description="Название предложенного блюда или напитка (как в меню)",
    )
    upsell_offered_id: str | None = Field(
        default=None,
        description="UUID позиции из меню (iiko), если рекомендация привязана к конкретной строке каталога — для аналитики",
    )
    upsell_reasoning: str | None = Field(
        default=None,
        description="Короткий аргумент: вкус, сочетаемость с заказом, традиция заведения — для логов и админки",
    )
    rejected_upsell_iiko_ids: list[str] = Field(
        default_factory=list,
        description=(
            "UUID номенклатуры iiko (`[id: …]` в меню), от которых клиент явно отказался в этом сообщении "
            "(например после вашей рекомендации). Пустой список, если отказа к доп. позициям не было."
        ),
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
    order_type: Literal["delivery", "pickup", "hall", ""] = Field(
        default="",
        description=(
            "Как клиент получит заказ: delivery — доставка (нужен адрес), "
            "pickup — самовывоз (время получения), "
            "hall — в зале ресторана (может сочетаться с бронью / предзаказом). "
            "Пустая строка — клиент ещё не указал, нужно спросить."
        ),
    )
    payment_method: Literal["cash", "card", "remote", ""] = Field(
        default="",
        description="При payment_mode=single: способ оплаты. Пустая строка — клиент ещё не указал.",
    )
    payment_mode: Literal["single", "mixed"] = Field(
        default="single",
        description="single — один способ (payment_method); mixed — точные суммы в payment_split, сумма = итог заказа",
    )
    payment_split: PaymentSplit = Field(
        default_factory=PaymentSplit,
        description="При payment_mode=mixed: сколько наличными / картой / удалённо (сумма трёх полей = итог)",
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
    guest_count_for_meal: int | None = Field(
        default=None,
        description=(
            "Сколько человек будет есть этот заказ (не путать с бронью). "
            "Если клиент сказал «нас четверо», «мы вдвоём» — укажи число. Если неизвестно — null."
        ),
    )
    dietary_allergy_notes: str = Field(
        default="",
        description=(
            "Ограничения и пожелания: аллергии, без лука, вегетарианство, халяль и т.д. "
            "Кратко, как передаст кухне; пустая строка, если не обсуждалось."
        ),
    )
    recognized_speech: str | None = Field(
        default=None,
        description=(
            "Только при голосовом вводе: дословная расшифровка речи клиента. "
            "В обычном текстовом чате — null (не заполнять)."
        ),
    )

    # Поля, для которых `null` от LLM семантически эквивалентен «не указано» и
    # должен быть схлопнут в дефолт. Это защищает схему от распространённой
    # привычки LLM заполнять все ключи `null` вместо пропуска ключа.
    # Списком, а не «молча для всех», чтобы непредвиденные `None` в новых полях
    # падали явно и мы могли осознанно решить — терпеть или нет.
    _LLM_NULLABLE_TO_DEFAULT: ClassVar[frozenset[str]] = frozenset({
        "order_type",
        "payment_method",
        "payment_mode",
        "payment_split",
        "is_preorder",
        "delivery_address",
        "pickup_time_note",
        "detected_language",
        "is_recommendation",
        "items",
        "order_actions",
        "rejected_upsell_iiko_ids",
        "guest_count_for_meal",
        "dietary_allergy_notes",
    })

    @model_validator(mode="before")
    @classmethod
    def _coerce_llm_nulls_to_defaults(cls, data: Any) -> Any:
        """
        LLM часто возвращают `null` для всех полей, включая не-nullable с дефолтом
        (наблюдалось на Gemini: order_type=null, payment_split=null, is_preorder=null).
        Удаляем такие ключи до валидации — Pydantic подставит `default` / `default_factory`.

        Работаем только с dict-входом (model_validate_json / model_validate на dict);
        при уже собранной модели `mode="before"` получит объект и просто пропустит его.
        """
        if not isinstance(data, dict):
            return data
        for field_name in cls._LLM_NULLABLE_TO_DEFAULT:
            if data.get(field_name, "__SENTINEL__") is None:
                data.pop(field_name, None)
        return data

    @model_validator(mode="after")
    def _normalize_payment(self) -> "AIBrainResponse":
        if self.payment_mode != "mixed":
            object.__setattr__(self, "payment_split", PaymentSplit())
        return self
