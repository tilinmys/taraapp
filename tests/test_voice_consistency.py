"""Regressions for "the voice changes mid-conversation, or at the start".

Two independent causes, both fixed, both locked here:

1. The TTS FallbackAdapter's second tier was OpenAI `alloy` - an ENGLISH voice.
   TTS is clause-buffered (one synthesis request per clause), so a single slow
   clause failed over and came back in English while the clause on either side
   stayed Kannada: the voice switching inside one sentence.

2. Four different numbers each claimed to be "the pace" (0.92 Sarvam, 0.92
   Gnani, 0.9 greeting, 1.08 session). The greeting therefore ran ~20% slower
   than the conversation that followed it - the same voice at a different
   tempo, one sentence into every call.
"""

import pytest

import agent
from livekit.plugins import gnani, openai
from sakhi_domain import Language


def _flatten(chain):
    """Providers inside a FallbackAdapter, unwrapping StreamAdapter layers."""
    out = []
    stack = [chain]
    while stack:
        node = stack.pop(0)
        # FallbackAdapter and StreamAdapter each name their children
        # differently across livekit-agents versions; check all of them.
        for attr in ("_tts_instances", "_tts", "_wrapped_tts"):
            inner = getattr(node, attr, None)
            if isinstance(inner, (list, tuple)):
                stack[:0] = [getattr(i, "tts", i) for i in inner]
                break
            if inner is not None:
                stack.insert(0, inner)
                break
        else:
            out.append(node)
    return out


# --- Cause 1: no cross-language failover -----------------------------------

@pytest.mark.parametrize("provider,voice", [("sarvam", "manisha"), ("gnani", "Janaki")])
def test_fallback_chain_never_leaves_the_target_language(provider, voice, monkeypatch):
    """Every tier must be an Indic provider. An English tier is the bug.

    This exercises build_tts()'s fallback-chain construction directly, which
    is now OFF by default (VOICE_TTS_FALLBACK_ENABLED=false in .env,
    2026-09-02 - single-provider Vakyam for the pilot, see that file's
    comment). The chain-building code itself is unchanged and still real;
    this test pins the flag on to keep testing it in isolation rather than
    inheriting whatever the ambient .env happens to default to.
    """
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam")
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani")
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "true")
    monkeypatch.delenv("VOICE_TTS_ENGLISH_LAST_RESORT", raising=False)

    tiers = _flatten(agent.build_tts(tts_provider=provider, voice_id=voice,
                                     language=Language.KANNADA))

    assert len(tiers) >= 2, "a single-tier chain means a TTS failure is dead air"
    for tier in tiers:
        # gnani.TTS (the official plugin, wired 2026-09-04 in place of the
        # old hand-rolled GnaniTTS wrapper) is itself literally named "TTS" -
        # a bare __name__ check can no longer tell it apart from the actual
        # forbidden English tier (openai.TTS), so identity is checked
        # directly against the one class this test must actually catch.
        assert not isinstance(tier, openai.TTS), (
            f"OpenAI TTS is in the chain for provider={provider}; a failover "
            "would speak Kannada text in an English voice mid-sentence"
        )


@pytest.mark.parametrize("provider,voice", [
    ("sarvam", "manisha"),
    ("gnani", "Kaveri"),
])
def test_backup_tier_is_the_other_indic_provider(provider, voice, monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam")
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani")
    # Fallback chain is off by default in .env (2026-09-02) - see the
    # comment on the test above.
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "true")

    tiers = _flatten(agent.build_tts(tts_provider=provider, voice_id=voice,
                                     language=Language.KANNADA))

    if provider == "sarvam":
        assert type(tiers[0]).__name__ == "SarvamTTS"
        assert isinstance(tiers[1], gnani.TTS)
    else:
        assert isinstance(tiers[0], gnani.TTS)
        assert type(tiers[1]).__name__ == "SarvamTTS"


def test_smallest_ai_primary_is_not_misclassified_as_sarvam(monkeypatch):
    """2026-09-04 regression: build_tts()'s primary_name detection only
    checked isinstance-of-Vakyam / isinstance-of-Gnani and fell through to
    "sarvam" for anything else - including Smallest AI. Since that name only
    selects a same-language BACKUP a few lines later, a Smallest-AI-primary
    call with GNANI_API_KEY set would silently chain in a Gnani backup meant
    for an actual Sarvam primary: a provider (and voice) swap the moment one
    clause tripped the fallback ceiling - the exact "the voice changes mid-
    conversation" symptom this file's own module docstring is about, just
    for the third provider it didn't yet cover.
    """
    monkeypatch.setenv("SMALLEST_API_KEY", "test-smallest")
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "true")

    tiers = _flatten(agent.build_tts(tts_provider="smallest_ai", voice_id="meher",
                                     language=Language.KANNADA))

    # No same-language backup is defined for a Smallest AI primary today, so
    # the correct, honest result is a single-tier chain - NOT a silently
    # mismatched Gnani backup that was actually meant for a Sarvam primary.
    assert not any(isinstance(t, gnani.TTS) for t in tiers), (
        f"Smallest AI primary was misclassified and given a Gnani backup: "
        f"{[type(t).__name__ for t in tiers]}"
    )


def test_english_last_resort_is_opt_in_only(monkeypatch):
    """The English tier may exist, but only when explicitly asked for."""
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam")
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani")
    # Fallback chain is off by default in .env (2026-09-02) - see the
    # comment on the first test in this file.
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "true")
    monkeypatch.delenv("VOICE_TTS_ENGLISH_LAST_RESORT", raising=False)

    default_tiers = len(_flatten(agent.build_tts(tts_provider="sarvam",
                                                 voice_id="manisha",
                                                 language=Language.KANNADA)))
    assert default_tiers == 2, "English must not be in the chain by default"


# --- Cause 2: one pace everywhere ------------------------------------------

@pytest.mark.parametrize("provider,voice", [("sarvam", "manisha")])
def test_greeting_and_conversation_speak_at_the_same_pace(provider, voice):
    """Gnani dropped from this parametrize 2026-09-04: switched from the old
    hand-rolled GnaniTTS wrapper (which clamped a `speed` kwarg to a
    documented 0.85-1.15 range) to the official livekit-plugins-gnani
    package, whose GnaniTTSOptions dataclass has no pace/speed field at all -
    confirmed by reading it, not assumed. There is no longer a Gnani-side
    pace to compare against a Sarvam/greeting pace, so this specific
    invariant no longer applies to Gnani; see
    test_gnani_has_no_pace_control_and_that_is_a_known_gap below for what IS
    still checked for it."""
    greeting = agent._build_greeting_tts(provider, voice, language=Language.KANNADA)
    session = agent._build_sarvam_tts(voice, language=Language.KANNADA.sarvam_code)

    def pace(t):
        return getattr(t, "_speed", None) or getattr(t, "_pace", None)

    assert pace(greeting) == pace(session) == agent.default_tts_pace(), (
        f"{provider}: greeting={pace(greeting)} session={pace(session)}; a tempo "
        "change one sentence into the call reads as the voice changing"
    )


def test_gnani_has_no_pace_control_and_that_is_a_known_gap():
    """Documents, rather than silently drops, the real capability gap: the
    official Gnani plugin (wired 2026-09-04) has no speed/pace parameter -
    checked directly against GnaniTTSOptions, which has none. A
    speed_override passed to _build_gnani_tts() is accepted for call-
    signature parity with every other _build_*_tts() function and silently
    ignored, which is honest given the real API has no slot for it - the
    alternative (pretending to clamp a value that never reaches Gnani) would
    be the same kind of silent-drop bug already found once in this codebase
    (changed.md C12, the TTS-speed dashboard slider that never reached the
    provider)."""
    provider = agent._build_gnani_tts("Kaveri", speed_override=0.5, language=Language.KANNADA)
    assert not hasattr(provider, "_speed")
    assert not hasattr(provider, "_pace")


def test_greeting_cache_key_includes_pace(monkeypatch):
    """Retuning the pace must invalidate pre-rendered greetings."""
    monkeypatch.setenv("SARVAM_PACE", "1.0")
    slow = agent._greeting_cache_path("manisha")
    monkeypatch.setenv("SARVAM_PACE", "1.1")
    fast = agent._greeting_cache_path("manisha")
    assert slow != fast, (
        "cache key ignores pace, so a pace change serves greetings rendered at "
        "the old speed forever"
    )


def test_greeting_cache_key_separates_providers():
    """Sarvam 'vidya' and Gnani 'Vidya' must not share a cache entry."""
    assert agent._greeting_cache_path("sarvam-vidya") != agent._greeting_cache_path("gnani-Vidya")


def test_greeting_cache_key_separates_languages():
    """2026-09-08 regression: this used to hash the module-level Kannada-only
    GREETING_TEXT constant regardless of which language argument was passed,
    so a Hindi or English call would have looked up (or overwritten) the
    Kannada cache entry - discovered when the cache was finally wired into
    the live greeting path and no per-language key existed to wire it with."""
    kn = agent._greeting_cache_path("Ananya", Language.KANNADA)
    hi = agent._greeting_cache_path("Ananya", Language.HINDI)
    en = agent._greeting_cache_path("Ananya", Language.ENGLISH)
    assert len({kn, hi, en}) == 3


def test_greeting_cache_round_trips_through_a_real_tts_stream(tmp_path, monkeypatch):
    """load_cached_greeting/ensure_greeting_cache existed since before this
    session but were never called from the live entrypoint - built, never
    wired. This exercises the actual read/write pair end-to-end (a fake TTS
    stream standing in for a real provider) rather than just the cache-key
    math the two tests above already cover."""
    import asyncio

    from livekit import rtc

    monkeypatch.setattr(agent, "GREETING_CACHE_DIR", tmp_path)

    class _FakeEvent:
        def __init__(self, frame):
            self.frame = frame

    class _FakeStream:
        def __init__(self, frames):
            self._frames = frames

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for f in self._frames:
                yield _FakeEvent(f)

        async def aclose(self):
            return None

    class _FakeTTS:
        def synthesize(self, text):
            pcm = b"\x00\x01" * 800  # 800 s16le samples @ 16kHz = 50ms
            frame = rtc.AudioFrame(data=pcm, sample_rate=16000, num_channels=1, samples_per_channel=800)
            return _FakeStream([frame])

    async def _run():
        assert agent.load_cached_greeting("Ananya", Language.ENGLISH) is None
        await agent.ensure_greeting_cache("Ananya", _FakeTTS(), Language.ENGLISH)
        frames = agent.load_cached_greeting("Ananya", Language.ENGLISH)
        assert frames, "greeting was cached but could not be loaded back"
        assert sum(len(f.data) for f in frames) > 0
        # A different language must not see this call's cache entry.
        assert agent.load_cached_greeting("Ananya", Language.HINDI) is None

    asyncio.run(_run())
