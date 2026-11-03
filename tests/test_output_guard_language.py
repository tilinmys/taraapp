"""Phase 3 (WHO Safe AI): when the output gate trips, the refusal is spoken in
the call's own language, and nothing offers an action Sakhi cannot take.

Before: a drug name in a Kannada call was replaced with an English line ending
"Shall I book you an appointment?" - a language switch mid-screening, and a
promise Sakhi (which books nothing) could not keep.
"""

import pytest

import agent
import guardrails
from sakhi_domain import Language

FLAGGED = ["ತಾಯಿಗೆ paraceta", "mol 500 ", "mg ಕೊಡಿ. ", "ಮತ್ತು ವಿಶ್ರಾಂತಿ."]


async def _spoken(chunks, guard):
    async def gen():
        for c in chunks:
            yield c

    return "".join([piece async for piece in guard(gen())])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "language, code",
    [(Language.KANNADA, "kn"), (Language.HINDI, "hi"), (Language.ENGLISH, "en"), ("kn-IN", "kn"), ("hi", "hi")],
)
async def test_refusal_is_in_the_calls_language(language, code):
    spoken = await _spoken(FLAGGED, agent.medical_output_guard_for(language))
    assert guardrails.script_for("medical_advice", code) in spoken
    assert "paracetamol" not in spoken.lower() and "mg" not in spoken
    assert "ವಿಶ್ರಾಂತಿ" not in spoken  # the rest of a flagged reply is swallowed


@pytest.mark.asyncio
async def test_unknown_language_falls_back_to_kannada_not_english():
    spoken = await _spoken(FLAGGED, agent.medical_output_guard_for("xx"))
    assert guardrails.script_for("medical_advice", "kn") in spoken


@pytest.mark.asyncio
async def test_clean_kannada_reply_passes_untouched():
    clean = ["ತಾಯಿಯ ", "ಇಂದಿನ ", "ಬಿಪಿ ಎಷ್ಟು?"]
    assert await _spoken(clean, agent.medical_output_guard_for(Language.KANNADA)) == "".join(clean)


def test_no_refusal_offers_to_book_anything():
    texts = [guardrails.OUTPUT_REPLACEMENT] + [guardrails.script_for("medical_advice", c) for c in ("kn", "hi", "en")]
    for text in texts:
        assert "appointment" not in text.lower() and "book" not in text.lower()
        assert "ಅಪಾಯಿಂಟ್" not in text and "अपॉइंट" not in text


def test_the_call_pipeline_uses_the_language_bound_guard():
    source = open(agent.__file__, encoding="utf-8").read()
    assert "medical_output_guard_for(call_language)," in source
