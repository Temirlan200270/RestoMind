from app.api.webhooks import _greeting_reply, _is_plain_greeting


def test_plain_greeting_detected() -> None:
    assert _is_plain_greeting("Привет")
    assert _is_plain_greeting("Здравствуйте")
    assert _is_plain_greeting("Добрый день")


def test_greeting_with_intent_not_detected_as_plain() -> None:
    assert not _is_plain_greeting("Привет, хочу меню")
    assert not _is_plain_greeting("Здравствуйте, доставка есть?")


def test_greeting_reply_text() -> None:
    assert _greeting_reply() == "Здравствуйте! Чем могу помочь?"
