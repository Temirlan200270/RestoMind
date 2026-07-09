from __future__ import annotations

from pathlib import Path

import yaml


def test_messaging_gateway_rls_migration_covers_channel_tables() -> None:
    text = Path("alembic/versions/20260709_messaging_gateway_rls.py").read_text(encoding="utf-8")

    assert "conversations" in text
    assert "channel_connections" in text
    assert "channel_messages" in text
    assert "ENABLE ROW LEVEL SECURITY" in text
    assert "FORCE ROW LEVEL SECURITY" in text


def test_render_blueprint_includes_messaging_gateway_service() -> None:
    data = yaml.safe_load(Path("render.yaml").read_text(encoding="utf-8"))
    services = data.get("services") or []
    names = {svc.get("name") for svc in services}

    assert "restomind" in names
    assert "restomind-messaging-gateway" in names

    backend = next(svc for svc in services if svc.get("name") == "restomind")
    backend_env = {row.get("key") for row in backend.get("envVars") or []}
    assert "MESSAGING_GATEWAY_URL" in backend_env
    assert "MESSAGING_GATEWAY_SECRET" in backend_env

    gateway = next(svc for svc in services if svc.get("name") == "restomind-messaging-gateway")
    assert gateway.get("plan") == "free"
    assert gateway.get("dockerfilePath") == "./services/messaging-gateway/Dockerfile"
    assert "disk" not in gateway
    gateway_env = {row.get("key") for row in gateway.get("envVars") or []}
    assert "RESTOMIND_API_URL" in gateway_env
    assert "RESTOMIND_GATEWAY_SECRET" in gateway_env
