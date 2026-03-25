"""Мелкие проверки для голосовых сообщений WhatsApp (без сети)."""

from app.services.ai_brain import normalize_audio_mime


def test_normalize_audio_mime_ogg_with_codecs() -> None:
    assert normalize_audio_mime("audio/ogg; codecs=opus") == "audio/ogg"


def test_normalize_audio_mime_mp3() -> None:
    assert normalize_audio_mime("audio/mpeg") == "audio/mpeg"


def test_normalize_audio_mime_octet_stream_defaults_to_ogg() -> None:
    assert normalize_audio_mime("application/octet-stream") == "audio/ogg"
