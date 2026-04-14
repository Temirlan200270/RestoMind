"""
Бесплатный TTS через edge-tts (голоса Microsoft Edge).
Голос подбирается по detected_language из ответа LLM (ru/kk/en/uz).
"""

import logging

import edge_tts

from app.core.config import settings

logger = logging.getLogger(__name__)

# Нейронные голоса Edge TTS по коду языка ответа бота
EDGE_TTS_VOICE_BY_LANG: dict[str, str] = {
    "ru": "ru-RU-SvetlanaNeural",
    "kk": "kk-KZ-AigulNeural",
    "en": "en-US-EmmaNeural",
    "uz": "uz-UZ-MadinaNeural",
}


def edge_voice_for_language(language_code: str | None) -> str:
    """
    Возвращает ShortName голоса для edge-tts.
    Для ru учитывает EDGE_TTS_VOICE из .env (переопределение дефолта).
    """
    raw = (language_code or "ru").strip().lower()
    if raw not in EDGE_TTS_VOICE_BY_LANG:
        raw = "ru"
    if raw == "ru":
        custom = (settings.edge_tts_voice or "").strip()
        if custom:
            return custom
    return EDGE_TTS_VOICE_BY_LANG[raw]


async def synthesize_speech_mp3(text: str, *, language_code: str | None = None) -> bytes:
    """
    Синтез речи в MP3.
    language_code — ru | kk | en | uz (как в AIBrainResponse.detected_language); None ≈ ru.
    """
    stripped = (text or "").strip()
    if not stripped:
        return b""
    voice = edge_voice_for_language(language_code)
    communicate = edge_tts.Communicate(stripped, voice)
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            chunks.append(chunk["data"])
    return b"".join(chunks)
