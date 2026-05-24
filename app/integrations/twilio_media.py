"""
Конвертация аудио Twilio Media Streams (G.711 μ-law, 8 kHz, моно) в WAV PCM для Whisper (OpenAI STT).
Python 3.11–3.12: stdlib `audioop`. Python 3.13+: пакет `audioop-lts` (см. requirements.txt).
"""

import io
import sys
import wave

if sys.version_info >= (3, 13):
    audioop = None  # type: ignore[misc, assignment]
    for _mod in ("audioop_lts", "audioop"):
        try:
            if _mod == "audioop_lts":
                import audioop_lts as audioop  # type: ignore[import-untyped, no-redef]
            else:
                import audioop  # type: ignore[import-untyped, no-redef]
            break
        except ImportError:
            continue
else:
    import audioop  # type: ignore[deprecated]


def mulaw_to_linear_pcm16(mulaw_data: bytes) -> bytes:
    """Поток μ-law → little-endian int16 (ширина сэмпла 2 байта)."""
    if audioop is None:
        raise RuntimeError(
            "Нет модуля audioop: для Python 3.13+ установите audioop-lts "
            f"(текущая версия интерпретатора: {sys.version_info.major}.{sys.version_info.minor})."
        )
    return audioop.ulaw2lin(mulaw_data, 2)


def mulaw_8k_to_wav(mulaw_data: bytes, sample_rate: int = 8000) -> bytes:
    """
    Упаковка μ-law в WAV (PCM 16-bit), mime audio/wav для openai_transcribe_voice (Whisper).
    """
    pcm = mulaw_to_linear_pcm16(mulaw_data)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
