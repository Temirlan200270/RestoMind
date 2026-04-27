from app.services.intent_router import _sanitize_reply_before_order_card


def test_sanitize_reply_removes_model_order_card_lines() -> None:
    raw = (
        "Принял!\n\n"
        "✨ *Ваш заказ*\n"
        "• Плов × 1 — 2890 ₸\n"
        "• Ачучук × 1 — 990 ₸\n"
        "💰 Итого: 3880 ₸\n"
        "\n"
        "Подтвердите, пожалуйста."
    )
    cleaned = _sanitize_reply_before_order_card(raw)
    assert "Ваш заказ" not in cleaned
    assert "Итого" not in cleaned
    assert "× 1" not in cleaned
    assert "Подтвердите" in cleaned


def test_sanitize_reply_keeps_normal_text() -> None:
    raw = "Отлично, подскажите адрес доставки."
    assert _sanitize_reply_before_order_card(raw) == raw
