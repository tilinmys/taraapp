"""Gateway STT: ONLY the self-hosted Indic model. Gnani / Sarvam are never used for STT.

No network: sakhi_gateway._request is replaced by a fake that records every URL.
"""

from __future__ import annotations

import json
import struct

import pytest

import sakhi_gateway as gw

INDIC = "http://127.0.0.1:8795"


def wav(seconds: float = 1.0) -> bytes:
    n = int(16000 * seconds)
    data = b"\x00\x00" * n
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", len(data)) + data


class FakeNet:
    """Answers the Indic service as configured; any other URL is a test failure."""

    def __init__(self, indic):
        self.indic = indic  # dict -> JSON reply, bytes -> raw reply, Exception -> raised
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, timeout))
        assert url.startswith(INDIC), f"STT went to another provider: {url}"
        if isinstance(self.indic, Exception):
            raise self.indic
        return self.indic if isinstance(self.indic, bytes) else json.dumps(self.indic).encode()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    # Provider keys present (they serve TTS): STT must still never use them.
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")
    monkeypatch.setenv("INDIC_STT_URL", INDIC)


INDIC_OK = {"text": "ನನಗೆ ಇಪ್ಪತ್ನಾಲ್ಕು ವರ್ಷ", "en": "", "provider": "indic", "lang": "kn", "lid_confidence": 0.98, "mode": "native"}


def test_stt_is_the_indic_model_with_the_app_shape(monkeypatch):
    net = FakeNet(INDIC_OK)
    monkeypatch.setattr(gw, "_request", net)
    out = gw.stt(wav(), "kn")
    assert out["text"] == "ನನಗೆ ಇಪ್ಪತ್ನಾಲ್ಕು ವರ್ಷ"
    assert out["provider"] == "indic" and out["lang"] == "kn" and out["lid_confidence"] == 0.98
    assert set(out) >= {"text", "en", "ms", "provider", "lang", "lid_confidence"}
    url, timeout = net.calls[0]
    assert url == f"{INDIC}/api/stt/raw?lang=kn"
    assert timeout <= 6.0  # under the app's 8 s


def test_english_answer_fills_en(monkeypatch):
    monkeypatch.setattr(gw, "_request", FakeNet({"text": "I am 24 years old", "lang": "en", "lid_confidence": 0.9}))
    assert gw.stt(wav(), "en")["en"] == "I am 24 years old"


@pytest.mark.parametrize(
    "failure, status",
    [
        (gw.GatewayError(504, "Voice provider unreachable."), 504),
        (gw.GatewayError(500, "boom"), 500),
        (ConnectionResetError("reset"), 504),
        (b"<html>not json</html>", 502),
    ],
)
def test_service_failure_is_a_retryable_error_never_a_fallback(monkeypatch, failure, status):
    net = FakeNet(failure)  # FakeNet fails the test if any other provider is called
    monkeypatch.setattr(gw, "_request", net)
    with pytest.raises(gw.GatewayError) as err:
        gw.stt(wav(), "kn")
    assert err.value.status == status and status >= 500  # the app retries 5xx, then keeps the answer offline
    assert len(net.calls) == 1


def test_without_indic_url_stt_is_not_configured(monkeypatch):
    monkeypatch.delenv("INDIC_STT_URL")
    net = FakeNet(INDIC_OK)
    monkeypatch.setattr(gw, "_request", net)
    with pytest.raises(gw.GatewayError) as err:
        gw.stt(wav(), "hi")
    assert err.value.status == 503 and net.calls == []
    assert gw.health()["stt"] is False


def test_health_reports_indic_for_stt_and_the_provider_for_tts(monkeypatch):
    h = gw.health()
    assert h["stt"] is True and h["stt_provider"] == "indic"
    assert h["tts"] is True  # Gnani stays Sakhi's voice


def test_no_gnani_or_sarvam_stt_code_left():
    assert not hasattr(gw, "_gnani_stt") and not hasattr(gw, "_sarvam_stt")


def test_input_checks_run_before_any_call(monkeypatch):
    net = FakeNet(INDIC_OK)
    monkeypatch.setattr(gw, "_request", net)
    with pytest.raises(gw.GatewayError) as err:
        gw.stt(b"not a wav", "kn")
    assert err.value.status == 400
    with pytest.raises(gw.GatewayError) as err:
        gw.stt(wav(), "ta")
    assert err.value.status == 400
    assert net.calls == []
