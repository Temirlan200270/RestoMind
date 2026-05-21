"""Bidirectional bridge: Twilio Media Streams (μ-law 8 kHz) ↔ OpenAI Realtime (PCM16)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.integrations.twilio_client import twilio_hangup_on_call, twilio_speak_on_call
from app.integrations.twilio_media import mulaw_to_linear_pcm16
from app.services.voice_realtime.session import REALTIME_INPUT_RATE, REALTIME_OUTPUT_RATE, RealtimeVoiceSession

logger = logging.getLogger(__name__)

TWILIO_RATE = 8000
_FALLBACK_SAY = "Извините, голосовой ассистент временно недоступен. Напишите нам в WhatsApp."


def _audioop():
    try:
        import audioop
    except ImportError:
        audioop = None  # type: ignore[misc, assignment]
    if audioop is None:
        raise RuntimeError(
            f"audioop required for voice realtime (Python {sys.version_info.major}.{sys.version_info.minor})"
        )
    return audioop


def mulaw_8k_to_pcm16_24k(mulaw_data: bytes) -> bytes:
    """Twilio μ-law 8 kHz → PCM16 mono 24 kHz for OpenAI Realtime."""
    if not mulaw_data:
        return b""
    op = _audioop()
    pcm8 = op.ulaw2lin(mulaw_data, 2)
    pcm24, _ = op.ratecv(pcm8, 2, 1, TWILIO_RATE, REALTIME_INPUT_RATE, None)
    return pcm24


def pcm16_24k_to_mulaw_8k(pcm24: bytes) -> bytes:
    """OpenAI PCM16 24 kHz → Twilio μ-law 8 kHz."""
    if not pcm24:
        return b""
    op = _audioop()
    pcm8, _ = op.ratecv(pcm24, 2, 1, REALTIME_OUTPUT_RATE, TWILIO_RATE, None)
    return op.lin2ulaw(pcm8, 2)


async def run_realtime_voice_bridge(
    websocket: WebSocket,
    *,
    org_id: int,
    phone: str,
    call_sid: str,
    org_name: str = "",
    stream_sid: str = "",
) -> None:
    """
    Run until Twilio stops the stream or session max duration.
    On failure: Twilio Say + hangup (graceful fallback).
    """
    session = RealtimeVoiceSession(
        org_id=org_id,
        phone=phone,
        call_sid=call_sid,
        instructions=RealtimeVoiceSession.default_instructions(org_name),
    )
    stream_sid = (stream_sid or "").strip()
    max_sec = int(settings.voice_realtime_max_session_sec)

    try:
        await session.connect()
    except Exception as exc:
        logger.exception("Realtime connect failed callSid=%s: %s", call_sid, exc)
        await _graceful_voice_fallback(call_sid)
        return

    outbound_lock = asyncio.Lock()

    async def send_mulaw_to_twilio(mulaw: bytes) -> None:
        nonlocal stream_sid
        if not stream_sid or not mulaw:
            return
        payload = base64.b64encode(mulaw).decode("ascii")
        msg = {
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": payload},
        }
        async with outbound_lock:
            await websocket.send_text(json.dumps(msg))

    async def on_openai_audio(pcm24: bytes) -> None:
        try:
            mulaw = pcm16_24k_to_mulaw_8k(pcm24)
            await send_mulaw_to_twilio(mulaw)
        except Exception as exc:
            logger.warning("Twilio outbound audio encode: %s", exc)

    async def pump_twilio_in() -> None:
        nonlocal stream_sid
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ev = data.get("event")
            if ev == "connected":
                continue
            if ev == "start":
                st = data.get("start") or {}
                stream_sid = (st.get("streamSid") or data.get("streamSid") or "").strip()
                logger.info(
                    "Realtime Twilio stream start callSid=%s streamSid=%s",
                    call_sid[:16],
                    stream_sid[:16] if stream_sid else "",
                )
                continue
            if ev == "media":
                if not stream_sid:
                    continue
                media = data.get("media") or {}
                b64 = media.get("payload") or ""
                if b64:
                    try:
                        mulaw = base64.b64decode(b64)
                        pcm24 = mulaw_8k_to_pcm16_24k(mulaw)
                        await session.append_input_pcm16(pcm24)
                    except Exception as exc:
                        logger.debug("Twilio inbound audio: %s", exc)
                continue
            if ev == "stop":
                break

    async def pump_openai_out() -> None:
        await session.pump_openai_to_twilio(on_audio_delta=on_openai_audio)

    try:
        await asyncio.wait_for(
            asyncio.gather(pump_twilio_in(), pump_openai_out()),
            timeout=max_sec,
        )
    except TimeoutError:
        logger.info("Realtime session max duration callSid=%s", call_sid)
    except Exception as exc:
        logger.exception("Realtime bridge error callSid=%s: %s", call_sid, exc)
        await _graceful_voice_fallback(call_sid)
    finally:
        await session.close()


async def _graceful_voice_fallback(call_sid: str) -> None:
    if call_sid:
        await twilio_speak_on_call(call_sid, _FALLBACK_SAY)
        await twilio_hangup_on_call(call_sid)
