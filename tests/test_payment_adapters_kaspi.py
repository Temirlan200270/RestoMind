"""Kaspi Pay webhook adapter: HMAC-SHA256 verify + parse."""

import hashlib
import hmac
import json

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

import app.core.config as app_config
from app.services.payment_providers.kaspi import KaspiWebhookAdapter

KASPI_SECRET = "kaspi-test-secret-key"


def _make_raw(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _kaspi_sig(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _make_request(headers: dict[str, str]) -> Request:
    """Build a minimal Starlette Request with the given headers."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_verify_ok_x_kaspi_signature(monkeypatch):
    monkeypatch.setattr(app_config.settings, "kaspi_webhook_hmac_secret", KASPI_SECRET)
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({"txn_id": "TX1", "order_id": 42, "status": "SUCCESS"})
    sig = _kaspi_sig(KASPI_SECRET, raw)
    req = _make_request({"X-Kaspi-Signature": sig})
    assert await adapter.verify(req, raw) is True


@pytest.mark.asyncio
async def test_verify_ok_x_hub_signature_256(monkeypatch):
    monkeypatch.setattr(app_config.settings, "kaspi_webhook_hmac_secret", KASPI_SECRET)
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({"txn_id": "TX2", "order_id": 7, "status": "SUCCESS"})
    sig = "sha256=" + _kaspi_sig(KASPI_SECRET, raw)
    req = _make_request({"X-Hub-Signature-256": sig})
    assert await adapter.verify(req, raw) is True


@pytest.mark.asyncio
async def test_verify_ok_x_signature(monkeypatch):
    monkeypatch.setattr(app_config.settings, "kaspi_webhook_hmac_secret", KASPI_SECRET)
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({"txn_id": "TX3", "order_id": 5, "status": "APPROVED"})
    sig = _kaspi_sig(KASPI_SECRET, raw)
    req = _make_request({"X-Signature": sig})
    assert await adapter.verify(req, raw) is True


@pytest.mark.asyncio
async def test_verify_bad_sig(monkeypatch):
    monkeypatch.setattr(app_config.settings, "kaspi_webhook_hmac_secret", KASPI_SECRET)
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({"txn_id": "TX4", "order_id": 1, "status": "SUCCESS"})
    req = _make_request({"X-Kaspi-Signature": "deadbeef"})
    assert await adapter.verify(req, raw) is False


@pytest.mark.asyncio
async def test_verify_no_secret(monkeypatch):
    monkeypatch.setattr(app_config.settings, "kaspi_webhook_hmac_secret", "")
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({"txn_id": "TX5", "order_id": 1, "status": "SUCCESS"})
    sig = _kaspi_sig(KASPI_SECRET, raw)
    req = _make_request({"X-Kaspi-Signature": sig})
    assert await adapter.verify(req, raw) is False


@pytest.mark.asyncio
async def test_verify_no_header(monkeypatch):
    monkeypatch.setattr(app_config.settings, "kaspi_webhook_hmac_secret", KASPI_SECRET)
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({"txn_id": "TX6", "order_id": 1, "status": "SUCCESS"})
    req = _make_request({})
    assert await adapter.verify(req, raw) is False


@pytest.mark.asyncio
async def test_parse_success():
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({
        "txn_id": "KASPI_TXN_99",
        "order_id": 123,
        "org_id": 7,
        "status": "SUCCESS",
        "amount": 12500.0,
    })
    parsed = await adapter.parse(raw)
    assert parsed.payment_id == "KASPI_TXN_99"
    assert parsed.order_id == 123
    assert parsed.organization_id == 7
    assert parsed.status == "paid"
    assert parsed.amount == 12500.0


@pytest.mark.asyncio
async def test_parse_failed():
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({
        "txn_id": "KASPI_TXN_100",
        "order_id": 5,
        "status": "FAILED",
        "amount": 500.0,
    })
    parsed = await adapter.parse(raw)
    assert parsed.status == "failed"


@pytest.mark.asyncio
async def test_parse_approved_alias():
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({"txn_id": "T", "order_id": 1, "status": "APPROVED"})
    parsed = await adapter.parse(raw)
    assert parsed.status == "paid"


@pytest.mark.asyncio
async def test_parse_payment_id_fallback():
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({"payment_id": "ALT_ID", "order_id": 99, "status": "SUCCESS"})
    parsed = await adapter.parse(raw)
    assert parsed.payment_id == "ALT_ID"


@pytest.mark.asyncio
async def test_parse_no_payment_id_uses_fallback():
    adapter = KaspiWebhookAdapter()
    raw = _make_raw({"order_id": 77, "status": "PAID"})
    parsed = await adapter.parse(raw)
    assert parsed.payment_id.startswith("kaspi_")
    assert parsed.status == "paid"
