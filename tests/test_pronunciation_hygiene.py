"""Pronunciation/text-hygiene mock test, 2026-09-09 - 10 ASHA worker
personas (5 Kannada, 5 Hindi), pure mock: no live microphone, no real
STT/LLM/TTS network calls, zero API tokens spent. Every persona's "agent
reply" text is a literal string run through this codebase's REAL,
LOCALLY-DEFINED text-hygiene functions (sanitize_text,
indian_english_phonetic_normalization, filter_code_artifacts,
sakhi_kannada.number_normalization_for) - the exact functions a real call
pipes spoken text through, just without the network calls around them.

What this specifically guards against (all confirmed live/real, not
hypothetical, per this session's own history):

1. THE "DOT X" BUG: sentence-final punctuation immediately followed by the
   next word with no space, which a TTS engine reads as "dot" - e.g.
   "Good morning, Anjali.Which month..." was a real, live agent_speech
   event. Guarded by sanitize_text()'s _MISSING_SENTENCE_SPACE_RE.
2. CROSS-LANGUAGE NUMBER LEAKAGE (found writing this test): Kannada number
   words spliced into Hindi/English text - "रक्तचाप 150 है" was becoming
   "रक्तचाप ನೂರ ಐವತ್ತು है" because kannada_number_normalization ran
   unconditionally regardless of call language. Fixed in sakhi_kannada.py/
   agent.py (number_normalization_for) the same day this test was written.
   Guarded here by asserting zero Kannada-script characters ever appear in
   a Hindi or English persona's processed output.
3. LEFTOVER DEBUG/VAD TAGS: "[VAD: FAST]" or similar literally spoken.
   Guarded by sanitize_text()'s TAG_CLEANER.
4. RAW UNCONVERTED DIGITS in Kannada speech (e.g. "150" read digit-by-digit
   or as an English loanword instead of "ನೂರ ಐವತ್ತು") - guarded by
   confirming Kannada personas' numbers DO get expanded.

Run: python -m pytest tests/test_pronunciation_hygiene.py -v -s
"""

from __future__ import annotations

import asyncio
import re
import sys

# Windows' console defaults to cp1252, which cannot encode Kannada/
# Devanagari - the whole point of this file. Without this, -s (needed to
# actually see the printed summary/tables) crashes on the first non-Latin
# print() rather than on any real assertion failure.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pytest

import agent
from sakhi_domain import Language
from sakhi_kannada import number_normalization_for


# --------------------------------------------------------------------------
# Harness: run a single string through the REAL transform functions, in the
# REAL order agent.py's entrypoint() registers them in (see the comment at
# that call site) - minus the framework-builtin "filter_markdown"/
# "filter_emoji" string-keyed transforms (resolved internally by
# livekit-agents, not locally-callable functions) and vad_tag_transform
# (a stateful class needing a live DynamicVADController - VAD-tag stripping
# is covered instead by sanitize_text()'s own TAG_CLEANER, which strips the
# same [VAD: ...] bracket shape as a final safety net regardless).
# --------------------------------------------------------------------------

async def _one_shot(text: str):
    yield text


async def _run_transform(transform_fn, text: str) -> str:
    parts = []
    async for chunk in transform_fn(_one_shot(text)):
        if chunk:
            parts.append(chunk)
    return "".join(parts)


def _pronounce(raw_llm_text: str, *, language: Language) -> str:
    """The full local pronunciation-hygiene pipeline a real turn's text
    actually goes through before reaching TTS, run synchronously for a
    single candidate string."""

    async def go():
        text = raw_llm_text
        text = await _run_transform(agent.filter_code_artifacts, text)
        text = await _run_transform(agent.indian_english_phonetic_normalization, text)
        text = await _run_transform(number_normalization_for(language), text)
        # sanitize_text() is not itself a streaming transform (it runs once
        # per finished sentence chunk inside tts_node) - applied last here
        # to match that real ordering.
        return agent.sanitize_text(text)

    return asyncio.run(go())


# A raw ASCII digit run, for checking Kannada expansion / Hindi leakage.
_DIGIT_RE = re.compile(r"\d")
_KANNADA_SCRIPT_RE = re.compile(r"[ಀ-೿]")
_MISSING_SPACE_RE = agent._MISSING_SENTENCE_SPACE_RE


def _assert_pronunciation_clean(spoken: str, *, language: Language, persona: str) -> None:
    assert not _MISSING_SPACE_RE.search(spoken), (
        f"[{persona}] unspaced sentence punctuation survived - TTS would read "
        f"this as 'dot': {spoken!r}"
    )
    assert "[" not in spoken and "]" not in spoken, (
        f"[{persona}] a debug/VAD tag bracket leaked into spoken text: {spoken!r}"
    )
    assert "{" not in spoken and "}" not in spoken, (
        f"[{persona}] a JSON/code artifact leaked into spoken text: {spoken!r}"
    )
    if language is Language.KANNADA:
        assert not _DIGIT_RE.search(spoken), (
            f"[{persona}] a raw digit was never expanded to Kannada words: {spoken!r}"
        )
    else:
        assert not _KANNADA_SCRIPT_RE.search(spoken), (
            f"[{persona}] Kannada-script text leaked into a {language.value} "
            f"utterance - the exact cross-language number-leak bug this test "
            f"file exists to catch: {spoken!r}"
        )


# ==========================================================================
# 10 personas - 5 Kannada, 5 Hindi. Each pairs a realistic ASHA-worker-call
# turn (drawn from real agent_speech patterns seen in this project's own
# logs, or deliberately shaped to probe one of the four bug classes above)
# with the language it was actually spoken in.
# ==========================================================================

PERSONAS: list[tuple[str, Language, str]] = [
    # --- Kannada (5) --------------------------------------------------
    ("ASHA Manjula (preeclampsia, dot-bug shape)", Language.KANNADA,
     "ಸರಿ, ಬರೆದುಕೊಂಡೆ.ಈಗ ಎಷ್ಟು ತಿಂಗಳು ಗರ್ಭ?"),
    ("ASHA Rekha (BP reading, must expand)", Language.KANNADA,
     "ರಕ್ತದೊತ್ತಡ 150/95 ಇದೆ. ತಲೆನೋವು ಇದೆಯ?"),
    ("ASHA Lakshmi (weight reading, must expand)", Language.KANNADA,
     "ತೂಕ 54 ಕೆಜಿ ಇದೆ. ಒಳ್ಳೆಯದು."),
    ("ASHA Kavya (VAD tag must not leak)", Language.KANNADA,
     "ಸರಿ, ಬರೆದುಕೊಂಡೆ. ತಲೆನೋವು ಇದೆಯ? [VAD: FAST]"),
    ("ASHA Deepa (gestational weeks, dot-bug shape)", Language.KANNADA,
     "ಒಳ್ಳೆಯದು.32 ವಾರ ಗರ್ಭ ಎಂದು ದಾಖಲಿಸಿದೆ."),
    # --- Hindi (5) -----------------------------------------------------
    ("ASHA Sunita (dot-bug shape)", Language.HINDI,
     "ठीक है, नोट कर लिया.अभी कितने महीने की गर्भवती है?"),
    ("ASHA Geeta (BP reading - must NOT become Kannada)", Language.HINDI,
     "रक्तचाप 150 है। सिरदर्द है क्या?"),
    ("ASHA Anjali (weight - must NOT become Kannada)", Language.HINDI,
     "वज़न 54 किलो है। अच्छा है।"),
    ("ASHA Poonam (VAD tag must not leak)", Language.HINDI,
     "अच्छा दीदी, नोट कर लिया। सूजन है क्या? [VAD: SLOW]"),
    ("ASHA Radha (gestational weeks, dot-bug shape)", Language.HINDI,
     "अच्छा.अट्ठाईस हफ्ते नोट कर लिया।"),
]


@pytest.mark.parametrize("persona,language,raw_text", PERSONAS, ids=[p[0] for p in PERSONAS])
def test_persona_speech_is_pronunciation_clean(persona, language, raw_text):
    spoken = _pronounce(raw_text, language=language)
    _assert_pronunciation_clean(spoken, language=language, persona=persona)
    print(f"\n[{persona}] ({language.value})")
    print(f"  raw:    {raw_text!r}")
    print(f"  spoken: {spoken!r}")


def test_all_ten_personas_summary_and_zero_cross_language_leakage():
    """Terminal summary across all 10 personas in one pass, plus the one
    assertion that matters most end-to-end: no Kannada number words ever
    reach a non-Kannada persona's final spoken text, and no raw digit ever
    reaches a Kannada persona's."""
    print("\n=== Pronunciation hygiene — 10 ASHA persona mock summary ===")
    print(f"{'persona':<45}{'lang':<10}{'clean':>8}")
    failures = []
    for persona, language, raw_text in PERSONAS:
        spoken = _pronounce(raw_text, language=language)
        try:
            _assert_pronunciation_clean(spoken, language=language, persona=persona)
            status = "OK"
        except AssertionError as exc:
            status = "FAIL"
            failures.append(str(exc))
        print(f"{persona:<45}{language.value:<10}{status:>8}")
    assert not failures, "\n".join(failures)


def test_kannada_bp_reading_actually_expands_to_words():
    """Positive check, not just absence-of-bug: a Kannada BP reading must
    become real Kannada number words, not merely "not a raw digit"."""
    spoken = _pronounce("ರಕ್ತದೊತ್ತಡ 150/95 ಇದೆ.", language=Language.KANNADA)
    assert "ನೂರ" in spoken  # "hundred" - part of both 150 and 95's expansion
    assert "150" not in spoken and "95" not in spoken


def test_hindi_bp_reading_keeps_its_digit_rather_than_turning_kannada():
    """The regression this whole file exists for, isolated to one turn:
    before the fix, this exact input produced Kannada words mid-Hindi-
    sentence. Post-fix, the digit survives as a digit (Hindi has no local
    word-expansion transform of its own - the model is expected to speak
    numbers as words per prompt rule #5) rather than becoming wrong-language
    text, which is the actually dangerous failure mode."""
    spoken = _pronounce("रक्तचाप 150 है।", language=Language.HINDI)
    assert not _KANNADA_SCRIPT_RE.search(spoken), (
        f"Kannada script leaked into Hindi text: {spoken!r}"
    )
