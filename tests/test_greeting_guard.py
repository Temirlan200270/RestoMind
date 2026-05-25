from app.services.quick_replies import greeting_reply, is_plain_greeting


def test_plain_greeting_detected() -> None:
    assert is_plain_greeting("Привет")
    assert is_plain_greeting("Здравствуйте")
    assert is_plain_greeting("Добрый день")


def test_greeting_with_intent_not_detected_as_plain() -> None:
    assert not is_plain_greeting("Привет, хочу меню")
    assert not is_plain_greeting("Здравствуйте, доставка есть?")


def test_greeting_reply_text() -> None:
    assert greeting_reply() == "Здравствуйте! Чем могу помочь?"
