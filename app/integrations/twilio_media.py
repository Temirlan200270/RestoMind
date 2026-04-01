"""
Конвертация аудио Twilio Media Streams (G.711 μ-law, 8 kHz, моно) в WAV PCM для Gemini.
Использует пакет audioop-lts (stdlib audioop удалён в Python 3.13+).
"""

import io
import wave

try:
    import audioop
except ImportError:
    audioop = None  # type: ignore[misc, assignment]


def mulaw_to_linear_pcm16(mulaw_data: bytes) -> bytes:
    """Поток μ-law → little-endian int16 (ширина сэмпла 2 байта)."""
    if audioop is None:
        raise RuntimeError("Установите пакет audioop-lts: pip install audioop-lts")
    return audioop.ulaw2lin(mulaw_data, 2)


def mulaw_8k_to_wav(mulaw_data: bytes, sample_rate: int = 8000) -> bytes:
    """
    Упаковка μ-law в WAV (PCM 16-bit), mime audio/wav для gemini_transcribe_voice.
    """
    pcm = mulaw_to_linear_pcm16(mulaw_data)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
