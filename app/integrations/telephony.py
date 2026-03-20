"""
Telephony Integration — фундамент голосового AI (v2.0).
Архитектура: Twilio SIP → WebSocket → STT (Deepgram/Whisper) → AI Brain → TTS → Audio Stream.
Модуль-заглушка — определяет интерфейсы и flow для будущей реализации.
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CallSession:
    """Сессия голосового вызова."""

    call_sid: str
    phone: str
    organization_id: int | None = None
    language: str = "ru-RU"


class STTProvider:
    """
    Интерфейс для Speech-to-Text провайдера.
    Реализации: DeepgramSTT, WhisperSTT.
    """

    async def transcribe(self, audio_chunk: bytes) -> str:
        raise NotImplementedError


class TTSProvider:
    """
    Интерфейс для Text-to-Speech провайдера.
    Реализации: ElevenLabsTTS, GoogleTTS.
    """

    async def synthesize(self, text: str, voice: str = "default") -> bytes:
        raise NotImplementedError


class TelephonyRouter:
    """
    Маршрутизатор голосовых вызовов.
    Flow: audio_in → STT → AI Brain → TTS → audio_out.
    """

    def __init__(
        self,
        stt: STTProvider,
        tts: TTSProvider,
    ) -> None:
        self.stt = stt
        self.tts = tts

    async def handle_audio_stream(
        self,
        session: CallSession,
        audio_chunk: bytes,
    ) -> bytes | None:
        """
        Обрабатывает аудио-чанк из WebSocket.

        Returns:
            Аудио-ответ (TTS) или None если ещё не набралось достаточно речи.
        """
        transcript = await self.stt.transcribe(audio_chunk)
        if not transcript.strip():
            return None

        logger.info("STT [%s]: %s", session.phone, transcript)

        from app.services.ai_brain import call_gemini
        from app.services.dialog_mgr import get_chat_history, append_to_history
        from app.db.session import redis_client

        history = await get_chat_history(redis_client, session.phone)
        ai_response = await call_gemini(history, transcript)

        await append_to_history(redis_client, session.phone, "user", transcript)
        await append_to_history(redis_client, session.phone, "assistant", ai_response.reply_text)

        logger.info("TTS [%s]: %s", session.phone, ai_response.reply_text[:80])

        audio_response = await self.tts.synthesize(ai_response.reply_text)
        return audio_response


# ─── Twilio WebSocket endpoint (для будущего подключения) ────────
# В main.py или отдельном роутере:
#
# @app.websocket("/voice/stream")
# async def voice_stream(ws: WebSocket):
#     session = CallSession(call_sid="...", phone="...")
#     router = TelephonyRouter(stt=DeepgramSTT(), tts=ElevenLabsTTS())
#     async for chunk in ws.iter_bytes():
#         response = await router.handle_audio_stream(session, chunk)
#         if response:
#             await ws.send_bytes(response)
