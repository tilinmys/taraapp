"""Regression coverage for the 2026-09-24 production STT silent-stall.

Browser audio reached LiveKit and local VAD, but the custom Sarvam stream sent
an obsolete v2-style wire contract.  Sarvam accepted the socket and then
returned neither a transcript nor an error, which also prevented LiveKit's
error-driven fallback adapter from moving to Deepgram.
"""

import base64
from urllib.parse import parse_qs, urlparse

from sarvam_wrappers import (
    SarvamSTT,
    _sarvam_stt_audio_message,
    _sarvam_stt_flush_message,
    _sarvam_stt_query,
    _ws_url,
)


def test_sarvam_stt_defaults_to_supported_saaras_v3(monkeypatch):
    monkeypatch.delenv("SARVAM_STT_CURRENT_PROTOCOL_ENABLED", raising=False)
    provider = SarvamSTT(api_key="mock", language_code="kn-IN")
    assert provider._model == "saaras:v3"
    assert provider._current_protocol is True


def test_current_stt_handshake_carries_turn_finalization_contract(monkeypatch):
    monkeypatch.delenv("SARVAM_STT_HIGH_VAD_SENSITIVITY", raising=False)
    monkeypatch.delenv("SARVAM_STT_FLUSH_SIGNAL_ENABLED", raising=False)

    query = _sarvam_stt_query(
        model="saaras:v3",
        language_code="kn-IN",
        mode="codemix",
        current_protocol=True,
    )
    uri = _ws_url("https://api.sarvam.ai", "/speech-to-text/ws", query)
    parsed = parse_qs(urlparse(uri).query)

    assert parsed["model"] == ["saaras:v3"]
    assert parsed["language-code"] == ["kn-IN"]
    assert parsed["mode"] == ["codemix"]
    assert parsed["sample_rate"] == ["16000"]
    assert "input_audio_codec" not in parsed
    assert parsed["vad_signals"] == ["true"]
    # OFF by default: "true" fragmented one answer into 3-4 context-free
    # finals and corrupted the age (docs/STT_FRAGMENTATION_INCIDENT.md).
    assert parsed["high_vad_sensitivity"] == ["false"]
    assert parsed["flush_signal"] == ["true"]


def test_current_stt_audio_is_raw_pcm_without_legacy_outer_type():
    pcm = b"\x01\x00\x02\x00"
    message = _sarvam_stt_audio_message(pcm, current_protocol=True)

    assert "type" not in message
    # Sarvam's production validator requires this label even though the body
    # is raw PCM; this is also what LiveKit's maintained plugin sends.
    assert message["audio"]["encoding"] == "audio/wav"
    assert message["audio"]["sample_rate"] == 16000
    assert base64.b64decode(message["audio"]["data"]) == pcm
    assert not base64.b64decode(message["audio"]["data"]).startswith(b"RIFF")


def test_stt_flush_and_legacy_kill_switch_are_explicit():
    assert _sarvam_stt_flush_message() == {"type": "flush"}

    legacy_query = _sarvam_stt_query(
        model="saarika:v2.5",
        language_code="kn-IN",
        mode="transcribe",
        current_protocol=False,
    )
    assert legacy_query == {"language-code": "kn-IN", "model": "saarika:v2.5"}

    legacy_audio = _sarvam_stt_audio_message(b"\0\0", current_protocol=False)
    assert legacy_audio["type"] == "audio"
    assert legacy_audio["audio"]["encoding"] == "audio/wav"
    assert base64.b64decode(legacy_audio["audio"]["data"]).startswith(b"RIFF")
