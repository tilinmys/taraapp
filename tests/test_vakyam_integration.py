"""Vakyam AI onboarded 2026-08-30 as the new default TTS provider, after a
live latency benchmark against real Kannada clinical speech showed ~130ms
median time-to-first-byte over its realtime WebSocket - decisively faster
than Sarvam (573ms) or Gnani (984ms) on the same style of phrase (see
vakyam_wrappers.py's module docstring and logs/vakyam_benchmark.json).

Vakyam has no STT product at all (confirmed against its own API reference),
so build_stt() is untouched by Vakyam specifically - but it separately gained
a real fallback chain (Deepgram <-> Sarvam) here, since it previously had
none and a single provider outage left the agent completely deaf on live
calls.
"""

import pytest

import agent
from livekit.plugins import gnani
from sakhi_domain import Language
from vakyam_wrappers import (
    VAKYAM_DEFAULT_VOICE,
    VAKYAM_KANNADA_VOICES,
    VakyamTTS,
    resolve_vakyam_voice,
)


# --- voice resolution --------------------------------------------------

def test_default_voice_is_ananya():
    """The tightest, most reproducible voice across 4 independent benchmark
    runs (128.8/129.8/131.0/132.1ms) - see the module docstring."""
    assert VAKYAM_DEFAULT_VOICE == "Ananya"
    assert resolve_vakyam_voice(None) == "Ananya"


def test_voice_resolution_is_case_insensitive():
    assert resolve_vakyam_voice("vidya") == "Vidya"
    assert resolve_vakyam_voice("PRIYA") == "Priya"


def test_unknown_voice_falls_back_to_default():
    assert resolve_vakyam_voice("NotARealVoice") == VAKYAM_DEFAULT_VOICE


def test_only_female_voices_are_offered():
    """Sakhi's persona is a warm female 'elder sister' voice across every
    existing provider (Sarvam manisha, Gnani Kaveri) - this integration must
    not silently introduce a male voice as an option."""
    for voice in VAKYAM_KANNADA_VOICES.values():
        assert voice["gender"] == "female"


# --- VakyamTTS construction ----------------------------------------------

def test_vakyam_tts_defaults():
    t = VakyamTTS(api_key="test-key")
    assert t.voice == "Ananya"
    assert t.sample_rate == 24000
    assert t.num_channels == 1
    assert t.capabilities.streaming is False


def test_vakyam_tts_speed_is_clamped_to_documented_range():
    assert VakyamTTS(api_key="k", speed=0.1)._speed == 0.5
    assert VakyamTTS(api_key="k", speed=5.0)._speed == 2.0


# --- a real Vakyam error must not itself crash on the way up --------------

@pytest.mark.asyncio
async def test_vakyam_error_with_a_non_numeric_code_does_not_typeerror(monkeypatch):
    """Confirmed live 2026-09-02 and again 2026-09-08: a real Vakyam
    {"type":"error", ...} payload's error.code is not guaranteed to be an
    HTTP-style int - livekit-agents' own APIStatusError.__init__ does
    `400 <= status_code < 500`, which TypeErrors comparing int to str. The
    agent could then never report what Vakyam actually said - only this
    TypeError about its own error-reporting path, and with
    VOICE_TTS_FALLBACK_ENABLED=false there was nothing to fail over to
    either. Reproduces the exact payload shape from the live incident
    against a mocked websocket, so a future regression here fails a test
    instead of only showing up mid-call.
    """
    import json

    from livekit.agents import APIStatusError
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
    from vakyam_wrappers import VakyamChunkedStream

    class _FakeWS:
        def __init__(self):
            self._to_send = [
                json.dumps({"type": "connected"}),
                json.dumps({"type": "configured"}),
                json.dumps({"type": "error", "error": {"code": "invalid_request", "message": "bad text"}}),
            ]

        async def recv(self):
            return self._to_send.pop(0)

        async def send(self, _data):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("websockets.connect", lambda *a, **k: _FakeWS())

    tts = VakyamTTS(api_key="k", voice="Ananya", language="kn-IN")
    stream = VakyamChunkedStream(tts_instance=tts, text="hello", conn_options=DEFAULT_API_CONNECT_OPTIONS)

    class _FakeEmitter:
        def initialize(self, **kwargs):
            pass

        def push(self, data):
            pass

        def flush(self):
            pass

    # _run()'s own except clause re-raises APIStatusError/APIConnectionError
    # unchanged (only OTHER exceptions get wrapped) - a TypeError from the
    # bug this guards against would surface as TypeError, not this.
    with pytest.raises(APIStatusError) as exc_info:
        await stream._run(_FakeEmitter())

    # The REAL Vakyam error must survive, not be replaced by the TypeError
    # this bug used to raise instead - and the sentinel must be the SDK's
    # own documented "unknown status" value, -1, never a fabricated 4xx/5xx.
    assert exc_info.value.status_code == -1
    assert "invalid_request" in str(exc_info.value) or "bad text" in str(exc_info.value)


# --- build_tts(): Vakyam-primary, Gnani+Sarvam fallback -------------------

def test_build_tts_defaults_to_sarvam_now_that_vakyam_is_out_of_credits(monkeypatch):
    """2026-09-08: the hardcoded fallback default flipped vakyam -> sarvam
    (a real live call the same day returned "account has 7 credits
    remaining but this request requires 60" - not a transient blip).
    Vakyam's own code and VakyamTTS class are untouched (see
    test_vakyam_tts_defaults etc. below, still passing) - only the DEFAULT
    when TTS_PROVIDER is unset changed, matching every earlier default swap
    in this file's history when the previous default became unusable."""
    monkeypatch.setenv("TTS_PROVIDER", "")  # force the .env-driven default
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "0")
    t = agent.build_tts(None, language=Language.KANNADA)
    from sarvam_wrappers import SarvamTTS

    assert isinstance(getattr(t, "_wrapped_tts", t), SarvamTTS)


def test_build_tts_chains_gnani_and_sarvam_as_fallback(monkeypatch):
    monkeypatch.setenv("VAKYAM_API_KEY", "test-vakyam-key")
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani-key")
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "1")
    t = agent.build_tts("vakyam", language=Language.KANNADA)
    assert type(t).__name__ == "FallbackAdapter"
    chain = t._tts_instances
    # gnani.TTS (the official plugin, wired 2026-09-04 in place of the old
    # hand-rolled GnaniTTS wrapper) is literally named "TTS" - ambiguous by
    # class name alone against openai.TTS/base tts.TTS, so identity is
    # checked by isinstance/module instead of __name__ for that one entry.
    assert isinstance(chain[0], VakyamTTS)
    assert isinstance(chain[1], gnani.TTS)
    assert type(chain[2]).__name__ == "SarvamTTS"


def test_build_tts_still_selectable_explicitly(monkeypatch):
    """tts_provider="gnani"/"sarvam" must still work exactly as before -
    Vakyam becoming the default must not remove the ability to pin a call to
    either existing provider."""
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani-key")
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "0")
    t = agent.build_tts("gnani", language=Language.KANNADA)
    assert isinstance(t, gnani.TTS)


def test_unknown_tts_provider_falls_through_to_vakyam(monkeypatch):
    monkeypatch.setenv("VAKYAM_API_KEY", "test-vakyam-key")
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "0")
    t = agent.build_tts("smallest", language=Language.KANNADA)  # stale/unknown provider name
    assert isinstance(t, VakyamTTS)


# --- build_stt(): the fallback chain that never existed before -----------

def test_build_stt_defaults_to_deepgram_with_sarvam_fallback(monkeypatch):
    monkeypatch.setenv("STT_PRIMARY", "")  # force the .env-driven default
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram-key")
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    monkeypatch.setenv("VOICE_STT_FALLBACK_ENABLED", "1")
    s = agent.build_stt(Language.KANNADA)
    assert type(s).__name__ == "FallbackAdapter"


def test_build_stt_sarvam_primary_with_deepgram_fallback(monkeypatch):
    """2026-09-08, explicit instruction: Sarvam saaras:v3/codemix as sole
    primary STT, Deepgram retained strictly as an async fallback for an
    unrecoverable outage - not Gnani (out as of this same instruction) and
    not disabled outright (unlike every earlier single-provider STT choice
    in .env's history) - this is the one deliberate exception, so it gets
    its own test rather than reusing the deepgram-primary one above."""
    from sarvam_wrappers import SarvamSTT

    monkeypatch.setenv("STT_PRIMARY", "sarvam")
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    monkeypatch.setenv("SARVAM_STT_MODEL", "saaras:v3")
    monkeypatch.setenv("SARVAM_STT_MODE", "codemix")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram-key")
    monkeypatch.setenv("VOICE_STT_FALLBACK_ENABLED", "1")
    s = agent.build_stt(Language.KANNADA)
    assert type(s).__name__ == "FallbackAdapter"
    # The primary inside the adapter must be the configured Sarvam model/mode,
    # not whatever SarvamSTT's own bare defaults happen to be. _stt_instances
    # is a real attribute (verified against the installed SDK's own
    # FallbackAdapter.__init__), not a guess.
    primary = s._stt_instances[0]
    assert isinstance(primary, SarvamSTT)
    assert primary._model == "saaras:v3"
    assert primary._mode == "codemix"


def test_build_stt_gnani_primary_with_deepgram_fallback(monkeypatch):
    """BUG FOUND 2026-09-09: build_stt()'s fallback-pairing logic had a
    branch for deepgram<->sarvam in both directions but NONE at all for
    gnani - confirmed live the same day, a real call hit "Gnani STT
    WebSocket error: HTTP 429" (recoverable=True) with nothing to fail over
    to, so the whole turn died with it despite VOICE_STT_FALLBACK_ENABLED
    being on. Fixed by adding the missing branch; this is the regression
    test for it."""
    from livekit.plugins import gnani as gnani_plugin

    monkeypatch.setenv("STT_PRIMARY", "gnani")
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani-key")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram-key")
    monkeypatch.setenv("VOICE_STT_FALLBACK_ENABLED", "1")
    s = agent.build_stt(Language.KANNADA)
    assert type(s).__name__ == "FallbackAdapter", (
        "gnani primary with a Deepgram key configured and fallback enabled "
        "must build a real fallback chain, not a bare, unprotected primary"
    )
    primary = s._stt_instances[0]
    assert isinstance(primary, gnani_plugin.STT)


def test_build_stt_without_a_backup_key_returns_bare_primary(monkeypatch):
    """No Sarvam key configured - must not crash trying to build a fallback
    it cannot construct, and must not silently drop the primary.

    STT_PRIMARY set explicitly (2026-09-09): this test used to rely on
    whatever STT_PRIMARY happened to be in the real .env at pytest-collection
    time to resolve to "deepgram" - true when .env's own default was
    deepgram, but broke silently the moment .env's STT_PRIMARY became
    "gnani" (with a real GNANI_API_KEY also present) and build_stt() grew a
    gnani<->deepgram fallback branch: the ambient env then legitimately
    built a FallbackAdapter, which isn't what this test is about at all.
    Isolating STT_PRIMARY here is the actual fix - not weakening the new
    gnani fallback branch this test's failure was really flagging as
    untested ambient-env coupling, not a regression in it.
    """
    monkeypatch.setenv("STT_PRIMARY", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram-key")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.setenv("VOICE_STT_FALLBACK_ENABLED", "1")
    s = agent.build_stt(Language.KANNADA)
    assert type(s).__name__ == "STT"  # deepgram.STT, not wrapped


def test_stt_fallback_can_be_disabled(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram-key")
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    monkeypatch.setenv("VOICE_STT_FALLBACK_ENABLED", "0")
    s = agent.build_stt(Language.KANNADA)
    assert type(s).__name__ != "FallbackAdapter"
