"""OpenAI Realtime API WebSocket session (PCM16 bidirectional)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any
from urllib.parse import urlencode

from app.core.config import settings
from app.services.voice_realtime.tools import REALTIME_TOOL_DEFINITIONS, dispatch_realtime_tool

logger = logging.getLogger(__name__)

OPENAI_REALTIME_BETA = "realtime=v1"
REALTIME_INPUT_RATE = 24_000
REALTIME_OUTPUT_RATE = 24_000


def _realtime_ws_url() -> str:
    base = (settings.openai_base_url or "https://api.openai.com").strip().rstrip("/")
    if base.startswith("https://"):
        host = base[len("https://") :]
        scheme = "wss"
    elif base.startswith("http://"):
        host = base[len("http://") :]
        scheme = "ws"
    else:
        host = base
        scheme = "wss"
    path = "/v1/realtime"
    q = urlencode({"model": settings.openai_realtime_model})
    return f"{scheme}://{host}{path}?{q}"


def _session_update_payload(*, instructions: str) -> dict[str, Any]:
    return {
        "type": "session.update",
        "session": {
            "modalities": ["text", "audio"],
            "instructions": instructions,
            "voice": settings.openai_realtime_voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": settings.openai_transcription_model},
            "turn_detection": {"type": "server_vad"},
            "tools": REALTIME_TOOL_DEFINITIONS,
            "tool_choice": "auto",
        },
    }


class RealtimeVoiceSession:
    """Manages one OpenAI Realtime WSS connection for a voice call."""

    def __init__(
        self,
        *,
        org_id: int,
        phone: str,
        call_sid: str,
        instructions: str,
    ) -> None:
        self.org_id = org_id
        self.phone = phone
        self.call_sid = call_sid
        self.instructions = instructions
        self._ws: Any = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    async def connect(self) -> None:
        api_key = (settings.openai_api_key or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets package is required for OpenAI Realtime") from exc

        url = _realtime_ws_url()
        extra_headers = {
            "Authorization": f"Bearer {api_key}",
            "OpenAI-Beta": OPENAI_REALTIME_BETA,
        }
        self._ws = await websockets.connect(url, additional_headers=extra_headers, ping_interval=20)
        self._connected = True
        await self._ws.send(json.dumps(_session_update_payload(instructions=self.instructions)))
        logger.info(
            "OpenAI Realtime connected callSid=%s org=%s",
            (self.call_sid[:12] + "…") if len(self.call_sid) > 12 else self.call_sid,
            self.org_id,
        )

    async def close(self) -> None:
        self._connected = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def append_input_pcm16(self, pcm16: bytes) -> None:
        if not pcm16 or not self.connected:
            return
        payload = {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm16).decode("ascii"),
        }
        await self._ws.send(json.dumps(payload))

    async def _handle_function_call(self, data: dict[str, Any]) -> None:
        name = str(data.get("name") or "")
        call_id = str(data.get("call_id") or "")
        args = str(data.get("arguments") or "{}")
        output = await dispatch_realtime_tool(
            name,
            args,
            org_id=self.org_id,
            phone=self.phone,
        )
        await self._ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    },
                }
            )
        )
        await self._ws.send(json.dumps({"type": "response.create"}))

    async def iter_events(self):
        """Async iterator of parsed Realtime server events."""
        if not self.connected:
            return
        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            yield data

    async def pump_openai_to_twilio(
        self,
        *,
        on_audio_delta: Any,
        on_transcript: Any | None = None,
    ) -> None:
        """Read OpenAI events; invoke callbacks for audio and tool calls."""
        async for data in self.iter_events():
            ev_type = str(data.get("type") or "")
            if ev_type == "error":
                err = data.get("error") or data
                logger.warning("OpenAI Realtime error: %s", err)
                raise RuntimeError(f"realtime_error:{err}")
            if ev_type == "response.audio.delta":
                b64 = data.get("delta") or ""
                if b64:
                    await on_audio_delta(base64.b64decode(b64))
                continue
            if ev_type == "response.audio_transcript.delta":
                if on_transcript:
                    delta = str(data.get("delta") or "")
                    if delta:
                        await on_transcript(delta)
                continue
            if ev_type == "response.function_call_arguments.done":
                await self._handle_function_call(data)
                continue
            if ev_type == "session.created":
                logger.debug("Realtime session.created callSid=%s", self.call_sid[:12])

    @staticmethod
    def default_instructions(org_name: str = "") -> str:
        label = org_name.strip() or "ресторан"
        return (
            f"Ты голосовой ассистент {label}. Отвечай кратко по-русски. "
            "Помогай с меню и направляй сложные заказы в WhatsApp через escalate_to_whatsapp."
        )
