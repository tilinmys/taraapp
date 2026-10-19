"""CachedTTS mirrors the wrapped provider's streaming capability (2026-09-25)."""

import pytest

from sarvam_wrappers import SafeSarvamRESTTTS, SarvamTTS
from tts_audio_cache import CachedTTS


def _sarvam(monkeypatch, streaming: str | None):
    if streaming is None:
        monkeypatch.delenv("SARVAM_TTS_STREAMING", raising=False)
    else:
        monkeypatch.setenv("SARVAM_TTS_STREAMING", streaming)
    return SarvamTTS(api_key="test-key", target_language_code="kn-IN")


def test_sarvam_streams_by_default_and_cache_does_not_reject_it(monkeypatch):
    provider = _sarvam(monkeypatch, None)
    assert provider.capabilities.streaming is True
    cached = CachedTTS(provider)  # used to raise ValueError
    assert cached.capabilities.streaming is True


def test_streaming_bypasses_cache_to_provider_stream(monkeypatch):
    provider = _sarvam(monkeypatch, "true")
    sentinel = object()
    seen = {}

    def fake_stream(*, conn_options):
        seen["conn_options"] = conn_options
        return sentinel

    monkeypatch.setattr(provider, "stream", fake_stream)
    assert CachedTTS(provider).stream() is sentinel
    assert "conn_options" in seen


def test_non_streaming_provider_keeps_cache_and_refuses_stream(monkeypatch):
    provider = _sarvam(monkeypatch, "false")
    cached = CachedTTS(provider)
    assert cached.capabilities.streaming is False
    with pytest.raises(NotImplementedError):
        cached.stream()


def test_rest_class_never_streams(monkeypatch):
    monkeypatch.setenv("SARVAM_TTS_STREAMING", "true")
    assert SafeSarvamRESTTTS(api_key="test-key").capabilities.streaming is False


def test_streamed_metrics_reach_the_fallback_adapter(monkeypatch):
    """Telemetry regression (2026-09-25): after streaming shipped, zero TTS
    metrics reached turn_latency - the provider's stream emits on the
    provider, and FallbackAdapter only listens on CachedTTS."""
    from types import SimpleNamespace
    from livekit.agents import tts as lk_tts

    provider = _sarvam(monkeypatch, "true")
    cached = CachedTTS(provider)
    chain = lk_tts.FallbackAdapter([cached])
    seen = []
    chain.on("metrics_collected", seen.append)

    provider.emit("metrics_collected", SimpleNamespace(streamed=True, ttfb=0.41))
    provider.emit("metrics_collected", SimpleNamespace(streamed=False, ttfb=1.6))
    assert [m.ttfb for m in seen] == [0.41]   # streamed relayed once; REST not doubled
