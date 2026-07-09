from __future__ import annotations


def test_app_imports_with_channel_routes() -> None:
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/channels/inbound" in paths
    assert "/api/channels/gateway/connections" in paths
    assert "/api/admin/channel-connections" in paths
    assert "/api/admin/channel-connections/health" in paths
