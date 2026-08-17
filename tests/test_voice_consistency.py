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
from gnani_wrappers import GnaniTTS
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
    """Every tier must be an Indic provider. An English tier is the bug."""
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam")
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani")
    monkeypatch.delenv("VOICE_TTS_ENGLISH_LAST_RESORT", raising=False)

    tiers = _flatten(agent.build_tts(tts_provider=provider, voice_id=voice,
                                     language=Language.KANNADA))

    assert len(tiers) >= 2, "a single-tier chain means a TTS failure is dead air"
    for tier in tiers:
        assert type(tier).__name__ != "TTS", (
            f"OpenAI TTS is in the chain for provider={provider}; a failover "
            "would speak Kannada text in an English voice mid-sentence"
        )


@pytest.mark.parametrize("provider,voice,backup_cls", [
    ("sarvam", "manisha", GnaniTTS),
    ("gnani", "Janaki", None),  # backup is SarvamTTS; asserted by name below
])
def test_backup_tier_is_the_other_indic_provider(provider, voice, backup_cls, monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam")
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani")

    tiers = _flatten(agent.build_tts(tts_provider=provider, voice_id=voice,
                                     language=Language.KANNADA))
    names = [type(t).__name__ for t in tiers]

    if provider == "sarvam":
        assert names[0] == "SarvamTTS" and names[1] == "GnaniTTS"
    else:
        assert names[0] == "GnaniTTS" and names[1] == "SarvamTTS"


def test_english_last_resort_is_opt_in_only(monkeypatch):
    """The English tier may exist, but only when explicitly asked for."""
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam")
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani")
    monkeypatch.delenv("VOICE_TTS_ENGLISH_LAST_RESORT", raising=False)

    default_tiers = len(_flatten(agent.build_tts(tts_provider="sarvam",
                                                 voice_id="manisha",
                                                 language=Language.KANNADA)))
    assert default_tiers == 2, "English must not be in the chain by default"


# --- Cause 2: one pace everywhere ------------------------------------------

@pytest.mark.parametrize("provider,voice", [("sarvam", "manisha"), ("gnani", "Janaki")])
def test_greeting_and_conversation_speak_at_the_same_pace(provider, voice):
    greeting = agent._build_greeting_tts(provider, voice, language=Language.KANNADA)
    session = (
        agent._build_gnani_tts(voice, language=Language.KANNADA)
        if provider == "gnani"
        else agent._build_sarvam_tts(voice, language=Language.KANNADA.sarvam_code)
    )

    def pace(t):
        return getattr(t, "_speed", None) or getattr(t, "_pace", None)

    assert pace(greeting) == pace(session) == agent.default_tts_pace(), (
        f"{provider}: greeting={pace(greeting)} session={pace(session)}; a tempo "
        "change one sentence into the call reads as the voice changing"
    )


def test_both_providers_share_one_pace_default():
    """Sarvam and Gnani must not disagree, or a failover changes speed."""
    sarvam = agent._build_sarvam_tts("manisha", language="kn-IN")
    gnani = agent._build_gnani_tts("Janaki", language=Language.KANNADA)
    assert getattr(sarvam, "_pace", None) == getattr(gnani, "_speed", None)


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
