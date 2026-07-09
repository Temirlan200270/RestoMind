from __future__ import annotations

from app.db.models import ChannelConnection
from app.services.channel_health import classify_connection_health


def test_classify_channel_connection_health() -> None:
    assert classify_connection_health(ChannelConnection(status="connected")) == "works"
    assert classify_connection_health(ChannelConnection(status="qr_required")) == "needs_reconnect"
    assert classify_connection_health(ChannelConnection(status="expired")) == "needs_reconnect"
    assert classify_connection_health(ChannelConnection(status="disabled")) == "blocked"
    assert classify_connection_health(ChannelConnection(status="banned")) == "blocked"
    assert classify_connection_health(ChannelConnection(status="rate_limited")) == "failed"
    assert classify_connection_health(ChannelConnection(status="error")) == "failed"
