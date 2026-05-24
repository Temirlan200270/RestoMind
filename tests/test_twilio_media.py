"""Тесты конвертации Twilio μ-law → WAV."""

import struct
import wave
from io import BytesIO

import pytest

from app.integrations.twilio_media import mulaw_8k_to_wav, mulaw_to_linear_pcm16, audioop as twilio_audioop


@pytest.mark.skipif(twilio_audioop is None, reason="нужен audioop или audioop-lts")
def test_mulaw_silence_to_pcm() -> None:
    """Тишина в μ-law часто кодируется как 0xFF."""
    pcm = mulaw_to_linear_pcm16(bytes([0xFF] * 160))
    assert len(pcm) == 320
    (s,) = struct.unpack_from("<h", pcm, 0)
    assert -50 < s < 50


@pytest.mark.skipif(twilio_audioop is None, reason="нужен audioop или audioop-lts")
def test_mulaw_8k_to_wav_header() -> None:
    raw = bytes([0xFF] * 8000)
    wav = mulaw_8k_to_wav(raw)
    with wave.open(BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 8000
        assert len(wf.readframes(wf.getnframes())) == 16_000
