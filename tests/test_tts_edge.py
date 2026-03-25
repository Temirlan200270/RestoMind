"""Выбор голоса edge-tts по языку ответа."""

from unittest.mock import patch

from app.services.tts_edge import EDGE_TTS_VOICE_BY_LANG, edge_voice_for_language


def test_voice_kk_en_uz_fixed() -> None:
    assert edge_voice_for_language("kk") == EDGE_TTS_VOICE_BY_LANG["kk"]
    assert edge_voice_for_language("en") == EDGE_TTS_VOICE_BY_LANG["en"]
    assert edge_voice_for_language("uz") == EDGE_TTS_VOICE_BY_LANG["uz"]


def test_voice_ru_env_override() -> None:
    with patch("app.services.tts_edge.settings") as s:
        s.edge_tts_voice = "ru-RU-DmitryNeural"
        assert edge_voice_for_language("ru") == "ru-RU-DmitryNeural"


def test_voice_ru_default_when_env_empty() -> None:
    with patch("app.services.tts_edge.settings") as s:
        s.edge_tts_voice = ""
        assert edge_voice_for_language("ru") == EDGE_TTS_VOICE_BY_LANG["ru"]


def test_voice_unknown_falls_back_to_ru() -> None:
    with patch("app.services.tts_edge.settings") as s:
        s.edge_tts_voice = ""
        assert edge_voice_for_language("xx") == EDGE_TTS_VOICE_BY_LANG["ru"]
