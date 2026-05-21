"""Focus-Driven OS Sprint 3 — Action Queue inbox + voice tail smoke."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_inbox_action_queue_template_markers():
    inbox = (REPO / "app" / "templates" / "screens" / "_tab_inbox.html").read_text(encoding="utf-8")
    assert "Action Queue" in inbox
    assert "moneyQueueStatusClass(item.severity)" in inbox
    assert "loadInboxActionQueue()" in inbox
    assert "ds-status-surface" in inbox


def test_inbox_mixin_registered_in_js():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "function adminMixinInboxActionQueue()" in js
    assert "adminMixinInboxActionQueue()," in js
    assert "loadInboxActionQueue()" in js
    assert "refreshVoiceCallStrip()" in js
    assert "locationQueryParams()" in js


def test_final_mile_voice_strip_uses_location_filter():
    ai_center = (REPO / "app" / "templates" / "screens" / "_tab_ai_center.html").read_text(encoding="utf-8")
    assert "refreshVoiceCallStrip()" in ai_center
    assert "loadMoreVoiceCallLogs()" in ai_center
    assert "voiceCallStatusSurfaceClass" in ai_center


def test_record_voice_call_accepts_location_id():
    voice_ai = (REPO / "app" / "services" / "voice_ai.py").read_text(encoding="utf-8")
    assert "location_id: int | None = None" in voice_ai
    assert 'merged_payload["location_id"]' in voice_ai
