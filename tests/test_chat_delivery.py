"""Юнит-тесты рангов статусов доставки WhatsApp."""

from app.services.chat_delivery import (
    delivery_status_rank,
    normalize_whatsapp_status,
    should_upgrade_delivery_status,
)


def test_delivery_status_rank_order() -> None:
    assert delivery_status_rank("sending") < delivery_status_rank("sent")
    assert delivery_status_rank("sent") < delivery_status_rank("delivered")
    assert delivery_status_rank("delivered") < delivery_status_rank("read")
    assert delivery_status_rank("read") < delivery_status_rank("failed")


def test_should_upgrade_monotonic() -> None:
    assert should_upgrade_delivery_status(None, "sent") is True
    assert should_upgrade_delivery_status("sending", "sent") is True
    assert should_upgrade_delivery_status("sent", "delivered") is True
    assert should_upgrade_delivery_status("delivered", "read") is True
    assert should_upgrade_delivery_status("sent", "sent") is False
    assert should_upgrade_delivery_status("delivered", "sent") is False
    assert should_upgrade_delivery_status("failed", "delivered") is False


def test_normalize_whatsapp_status() -> None:
    assert normalize_whatsapp_status("DELIVERED") == "delivered"
    assert normalize_whatsapp_status("read") == "read"
    assert normalize_whatsapp_status("nope") == ""
