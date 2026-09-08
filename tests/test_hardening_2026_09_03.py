"""Regression tests for the four production failure modes found by audit.

Each test here reproduces a SPECIFIC real trigger, not a generic shape of the
bug. That distinction is the whole point: a test asserting "an obviously fake
claim is blocked" proves almost nothing, while a test asserting "the exact
sentence that shipped on 2026-09-03 is blocked" proves the gap that actually
bit us is actually closed.

  §3  Backchannel swallowing - "yes"/"ಹೌದು" after a closed clinical question
      was discarded as agreement noise before the intake tracker saw it.
  §7  Unbacked clinical mutations - the model narrating a write that never
      happened.
  §8  Media race - greeting spoken before the participant's audio was
      subscribed.
  §4  Domain terminology - clinical terms and BP readings mangled by ASR.
"""

import asyncio

import pytest

import agent
from clinical_terms import CLINICAL_KEYWORDS, canonicalise
from sakhi_domain import Language, SakhiCallState
from sakhi_intake import QUESTIONS_BY_ID, is_assent


# ---------------------------------------------------------------------------
# §3  The backchannel trap
# ---------------------------------------------------------------------------

def _state_with_armed(vad: str) -> SakhiCallState:
    """A call state with the first question of the given VAD class armed."""
    state = SakhiCallState()
    state.intake.arm_next()
    while state.intake.pending_id and QUESTIONS_BY_ID[state.intake.pending_id].vad != vad:
        state.intake.record_answer("a genuinely substantive answer for this question")
        state.intake.arm_next()
    assert state.intake.pending_id, f"no {vad} question found in the questionnaire"
    return state


@pytest.mark.parametrize("word", ["yes", "yeah", "ok", "ಹೌದು", "ಹೂಂ", "हाँ"])
def test_assent_after_a_closed_question_is_an_answer_not_a_backchannel(word):
    """THE §3 BUG. Every one of these words matches is_backchannel()'s own
    regex - that is exactly why they were being thrown away. With a FAST
    (yes/no) question armed, each is a real answer and must survive the gate.
    """
    state = _state_with_armed("FAST")
    assert agent.is_backchannel(word), (
        "precondition: this word must still match the backchannel filter, "
        "otherwise this test is not exercising the bug"
    )
    assert agent._answers_pending_question(state, word, None) is True


@pytest.mark.parametrize("word", ["yes", "ಹೌದು", "हाँ"])
def test_the_same_word_is_still_a_backchannel_after_a_statement(word):
    """The other half of the fix. Context, not wording, is the distinction -
    if this ever returns True the backchannel filter has been defeated
    entirely and every grunt costs an LLM round trip again."""
    state = SakhiCallState()          # nothing armed
    assert agent._answers_pending_question(
        state, word, "I will note that down for her chart."
    ) is False


def test_assent_after_an_open_question_does_not_count():
    """A bare "yes" does not answer "what brought her in today?". Treating it
    as one is the false-advance bug from the other direction."""
    state = _state_with_armed("SLOW")
    assert agent._answers_pending_question(state, "yes", None) is False


def test_assent_exempted_when_sakhi_asked_a_question_outside_the_questionnaire():
    """Sakhi asks clarifying questions that are not one of the numbered
    inventory items. The armed-question check alone would miss those
    entirely."""
    state = SakhiCallState()
    assert agent._answers_pending_question(
        state, "yes", "Is that the same mother we spoke about last week?"
    ) is True


@pytest.mark.parametrize("utterance,expected", [
    ("Is she taking the iron tablets?", True),
    ("ಅವಳು ಮಾತ್ರೆ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದಾಳೆಯೇ?", True),
    ("I will note that down.", False),
    ("", False),
    (None, False),
])
def test_ends_in_question(utterance, expected):
    assert agent.ends_in_question(utterance) is expected


def test_a_long_utterance_is_never_exempted():
    """The exemption must be reachable only by a recognised one-word assent -
    otherwise it becomes a hole in the backchannel filter."""
    state = _state_with_armed("FAST")
    assert agent._answers_pending_question(
        state, "yes she has been taking them every morning", None
    ) is False


# ---------------------------------------------------------------------------
# §7  Unbacked clinical mutations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "I have recorded her blood pressure.",
    "I've logged the reading of 140 over 90.",
    "Her blood pressure has been saved to the chart.",
    "The symptoms are noted in her record.",
    "I have recorded it in her chart.",            # anaphoric - names no entity
    "ನಾನು ರಕ್ತದೊತ್ತಡ ದಾಖಲಿಸಿದ್ದೇನೆ.",
])
def test_completion_claims_are_detected(sentence):
    assert agent.claims_completed_write(sentence) is True


@pytest.mark.parametrize("sentence", [
    "I have not recorded her blood pressure yet.",
    "I couldn't save the reading.",
    "Let me record that now.",
    "What is her blood pressure?",
    "Her blood pressure is 140 over 90.",
    "I will record that once you confirm.",
    "",
])
def test_honest_and_neutral_sentences_are_not_claims(sentence):
    """An honest admission of failure must pass through untouched. A guard
    that blocked "I couldn't save that" would train the agent toward silence
    about failures - worse than the problem it fixes."""
    assert agent.claims_completed_write(sentence) is False


def test_unbacked_bp_claim_is_blocked_without_a_receipt():
    state = SakhiCallState()
    state.begin_turn()
    assert agent.unbacked_claim_reason(
        "I have recorded her blood pressure.", state
    ) == "blood_pressure"


def test_backed_bp_claim_passes_once_the_write_succeeded():
    state = SakhiCallState()
    state.begin_turn()
    state.bp_record_succeeded = True
    assert agent.unbacked_claim_reason(
        "I have recorded her blood pressure.", state
    ) is None


def test_anaphoric_claim_with_no_write_at_all_is_blocked():
    """"I have recorded it" names no entity, which is how a paraphrasing
    model most often phrases it - the entity was in the previous sentence.
    Routing on named entities alone silently passes exactly these."""
    state = SakhiCallState()
    state.begin_turn()
    assert agent.unbacked_claim_reason("I have recorded it in her chart.", state) == "unspecified"


def test_symptom_claim_is_not_authorised_by_a_bp_receipt():
    """Receipts are per-write, not a general "something succeeded" flag."""
    state = SakhiCallState()
    state.begin_turn()
    state.bp_record_succeeded = True
    assert agent.unbacked_claim_reason(
        "Her symptoms have been recorded.", state
    ) == "symptoms"


def test_receipts_expire_with_the_turn():
    """THE FLAG-LIFETIME BUG this guard would otherwise have. A successful
    write on an earlier turn must not authorise a claim made now."""
    state = SakhiCallState()
    state.begin_turn()
    state.bp_record_succeeded = True
    assert agent.unbacked_claim_reason("I have recorded her BP.", state) is None

    state.begin_turn()                                   # next user turn
    assert agent.unbacked_claim_reason("I have recorded her BP.", state) == "blood_pressure"


def test_guard_rewrites_only_the_offending_sentence_in_a_stream():
    """End-to-end over the streaming path: the factual statement and the
    follow-up question must survive untouched; only the claim is replaced."""
    state = SakhiCallState(language=Language.ENGLISH)
    state.begin_turn()

    chunks = ["Her ", "blood pressure ", "is 140 over 90. ",
              "I have recorded it ", "in her chart. ", "Is she taking iron?"]
    pending, spoken = "", []
    for chunk in chunks:
        pending += chunk
        while True:
            match = agent._SENTENCE_END_RE.search(pending)
            if not match:
                break
            sentence, pending = pending[: match.end()], pending[match.end():]
            reason = agent.unbacked_claim_reason(sentence, state)
            spoken.append(agent.bridge_line_for(state.language) if reason else sentence)
    if pending.strip():
        spoken.append(pending)

    result = "".join(spoken)
    assert "Her blood pressure is 140 over 90." in result
    assert "Is she taking iron?" in result
    assert "I have recorded it" not in result, "the unbacked claim was spoken"
    assert "let me put that into her chart" in result.lower()


def test_guard_rewrite_survives_sanitize_text_without_fusing_sentences():
    """Regression, 2026-09-04: the two tests above replicate tts_node's
    accumulation loop WITHOUT its actual sanitize_text() call, so they never
    caught this. sanitize_text() ends in .strip(), which eats the trailing
    space _SENTENCE_END_RE's match captured (and the one bridge_line_for()
    deliberately appends) - full_text_parts is then joined with NO separator,
    so consecutive sentences fuse with zero gap. Confirmed live: a real call
    spoke "...into her chart now.Please tell me the mother's name and age."
    as one run-on word boundary, reaching the TTS synthesizer that way, not
    just the log line. This exercises the REAL join logic (mirroring
    agent.py's tts_node) including the trailing-space fix, so a future
    regression here fails a test instead of only showing up on a live call.
    """
    state = SakhiCallState(language=Language.ENGLISH)
    state.begin_turn()

    chunks = ["I have recorded it ", "in her chart. ",
              "Please tell me the mother's name and age."]
    pending, parts = "", []
    for chunk in chunks:
        pending += chunk
        while True:
            match = agent._SENTENCE_END_RE.search(pending)
            if not match:
                break
            sentence, pending = pending[: match.end()], pending[match.end():]
            reason = agent.unbacked_claim_reason(sentence, state)
            vetted = agent.sanitize_text(
                agent.bridge_line_for(state.language) if reason else sentence
            )
            if vetted and sentence.endswith((" ", "\n", "\t")):
                vetted += " "
            if vetted:
                parts.append(vetted)
    if pending.strip():
        parts.append(agent.sanitize_text(pending))

    result = "".join(parts)
    assert "now.Please" not in result, f"sentences fused with no gap: {result!r}"
    assert "chart now. Please tell me" in result


def test_sanitize_text_inserts_a_space_the_llm_never_put_there():
    """The "Dot Anjali" bug, confirmed live 2026-09-09 on a real call:
    "Good morning, Anjali.Which month of pregnancy is she in?" and "Noted,
    Didi.When was her last menstrual period?" both reached TTS with no space
    at all - a different bug than test_guard_rewrite_survives_sanitize_text_
    without_fusing_sentences above, which preserves a space that already
    existed before .strip() would eat it. This is the LLM's own raw
    generation never having one in the first place, so sanitize_text() must
    actively insert it, not just preserve it."""
    assert agent.sanitize_text("Good morning, Anjali.Which month of pregnancy is she in?") == (
        "Good morning, Anjali. Which month of pregnancy is she in?"
    )
    assert agent.sanitize_text("Noted, Didi.When was her last menstrual period?") == (
        "Noted, Didi. When was her last menstrual period?"
    )
    # Must NOT split a decimal reading - the next character after the
    # period being a DIGIT, not a letter, is exactly what distinguishes a
    # missing sentence space from a number.
    assert agent.sanitize_text("BP is 150.5 mmHg today.") == "BP is 150.5 mmHg today."
    # Ellipsis and normal already-spaced text must survive untouched.
    assert agent.sanitize_text("Wait... what did she say?") == "Wait... what did she say?"


def test_the_audit_trail_records_what_was_spoken_not_what_was_generated():
    """Caught in review of this very change. tts_node accumulates
    `full_text_parts` for the "Sakhi said" clinical audit line, for
    `_last_agent_utterance` (which §3's question-detection reads), and for
    the adaptive-VAD evaluation. Collecting the RAW chunks there would log
    the blocked claim as though Sakhi had said it - on a clinical audit
    trail, precisely backwards, and it would also feed the §3 gate a
    sentence that was never actually spoken.

    This reproduces the accumulate-what-you-yield contract directly.
    """
    state = SakhiCallState(language=Language.ENGLISH)
    state.begin_turn()

    spoken_parts, pending = [], ""
    for chunk in ["I have recorded ", "her blood pressure. ", "Is she resting?"]:
        pending += chunk
        while True:
            match = agent._SENTENCE_END_RE.search(pending)
            if not match:
                break
            sentence, pending = pending[: match.end()], pending[match.end():]
            reason = agent.unbacked_claim_reason(sentence, state)
            spoken_parts.append(
                agent.bridge_line_for(state.language) if reason else sentence
            )
    if pending:
        spoken_parts.append(pending)

    transcript = "".join(spoken_parts)
    assert "I have recorded her blood pressure." not in transcript, (
        "the audit trail claims Sakhi said something she was prevented from saying"
    )
    assert "Is she resting?" in transcript


def test_bridge_line_is_spoken_in_the_call_language():
    assert "ದಾಖಲಿಸುತ್ತೇನೆ" in agent.bridge_line_for(Language.KANNADA)
    assert "दर्ज" in agent.bridge_line_for(Language.HINDI)
    assert "chart" in agent.bridge_line_for(Language.ENGLISH)


def test_bridge_line_ends_with_a_separator():
    """Substituted mid-stream for a sentence that carried its own separator -
    without one it runs into the next sentence and the clause tokenizer reads
    the pair as one run-on."""
    for lang in (Language.KANNADA, Language.HINDI, Language.ENGLISH):
        assert agent.bridge_line_for(lang).endswith(" ")


# ---------------------------------------------------------------------------
# §4  Clinical terminology canonicalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("her BP is 120 over 80", "her BP is 120/80 mmHg"),
    ("blood pressure one forty over ninety", "blood pressure 140/90 mmHg"),
    ("reading is 150 by 100", "reading is 150/100 mmHg"),
    ("150/100", "150/100 mmHg"),
])
def test_blood_pressure_is_normalised(raw, expected):
    assert canonicalise(raw) == expected


@pytest.mark.parametrize("raw", [
    "she has 2 over 3 children",      # not a vital sign
    "BP is 300 over 20",              # implausible; record_bp must reject it loudly
    "80 over 120",                    # diastolic >= systolic, physiologically impossible
])
def test_non_readings_are_left_alone(raw):
    """A guard that rewrote these would manufacture a plausible-looking vital
    sign out of something that was never one."""
    assert canonicalise(raw) == raw


@pytest.mark.parametrize("raw,term", [
    ("she has pre eclampsia signs", "preeclampsia"),
    ("protein urea was found", "proteinuria"),
    ("just national diabetes screening", "gestational diabetes"),
    ("the angan wadi worker said", "anganwadi"),
    ("she had convolutions last night", "convulsions"),
    ("her hemo globin is low", "hemoglobin"),
])
def test_mangled_clinical_terms_are_canonicalised(raw, term):
    assert term in canonicalise(raw)


def test_canonicalise_is_a_no_op_on_clean_text():
    clean = "She is in her sixth month and feels well."
    assert canonicalise(clean) == clean


def test_stt_keywords_are_weighted_tuples():
    """Tier 1 is passed straight to the vendor; a malformed entry would be
    rejected at STT construction, i.e. at the start of a real call."""
    assert CLINICAL_KEYWORDS, "keyword list is empty"
    for entry in CLINICAL_KEYWORDS:
        assert isinstance(entry, tuple) and len(entry) == 2, entry
        term, weight = entry
        assert isinstance(term, str) and term.strip()
        assert isinstance(weight, float) and 0 < weight <= 5


def test_the_highest_stakes_terms_are_boosted_hardest():
    weights = dict(CLINICAL_KEYWORDS)
    assert weights["preeclampsia"] >= weights["anganwadi"], (
        "a term whose miss can lose an emergency must outrank a place name"
    )


def test_bidar_geography_matches_across_both_stt_providers():
    """2026-09-04 UPDATE: this used to assert full parity between Deepgram's
    and Smallest AI's geography lists. That is no longer the invariant -
    Deepgram's real per-connection keyterm budget is much smaller than this
    list once assumed (a live call's WebSocket handshake was rejected
    outright with the full ~19-place list plus the full danger-sign list;
    see DEEPGRAM_MEDICAL_KEYTERMS's comment in agent.py for the bisection
    against the real API), and Smallest AI's `keywords` parameter has shown
    no equivalent hard cap. Deepgram's list is now deliberately SMALLER than
    Smallest AI's, not equal to it - asserting equality again would either
    force Smallest AI's richer list back down for no reason, or silently
    regress Deepgram's connection the same way this file's own history
    already broke a live call once.

    What's still checked as a real cross-file invariant: every place
    Deepgram's trimmed list DOES carry must also be in Smallest AI's list -
    Deepgram is the subset, never the other way around, so no place name
    fixed on the smaller list quietly vanishes from the richer one."""
    import agent

    weighted_terms = {term for term, _ in CLINICAL_KEYWORDS}
    for place in agent.DEEPGRAM_BIDAR_KEYTERMS:
        if place in ("taluk hospital", "district hospital", "PHC"):
            continue  # facility terms, not place names - not in CLINICAL_KEYWORDS's geography set
        assert place in weighted_terms, (
            f"{place!r} is in Deepgram's (smaller) list but missing from "
            "Smallest AI's (richer) one - Deepgram must stay a subset"
        )


# ---------------------------------------------------------------------------
# §8  Media readiness before the greeting
# ---------------------------------------------------------------------------

class _FakePublication:
    def __init__(self, kind, subscribed):
        self.kind = kind
        self.subscribed = subscribed


class _FakeParticipant:
    def __init__(self, publications):
        self.track_publications = {str(i): p for i, p in enumerate(publications)}


class _FakeRoom:
    """Minimal stand-in for rtc.Room's subscription surface."""

    def __init__(self, participants=()):
        self.remote_participants = {str(i): p for i, p in enumerate(participants)}
        self._handlers = {}

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def off(self, event, handler):
        self._handlers.get(event, []).remove(handler)

    def emit_audio_track(self):
        from livekit import rtc

        class _Track:
            kind = rtc.TrackKind.KIND_AUDIO

        for handler in list(self._handlers.get("track_subscribed", [])):
            handler(_Track(), None, None)


def test_greeting_waits_and_returns_immediately_when_audio_is_already_flowing():
    """The common case. Must not cost measurable time - this runs on every
    single call, and a wait that always costs 1.5s would be worse than the
    clipping it prevents."""
    from livekit import rtc

    room = _FakeRoom([_FakeParticipant([
        _FakePublication(rtc.TrackKind.KIND_AUDIO, subscribed=True)
    ])])

    async def _run():
        loop = asyncio.get_running_loop()
        started = loop.time()
        ready = await agent.wait_for_remote_audio(room, timeout=1.5)
        return ready, loop.time() - started

    ready, elapsed = asyncio.run(_run())
    assert ready is True
    assert elapsed < 0.05, f"already-subscribed path took {elapsed:.3f}s"


def test_greeting_waits_for_the_subscription_event():
    """The case the wait exists for: audio not yet flowing when we arrive."""
    room = _FakeRoom()

    async def _run():
        task = asyncio.create_task(agent.wait_for_remote_audio(room, timeout=1.5))
        await asyncio.sleep(0.01)          # let the waiter register its handler
        room.emit_audio_track()
        return await task

    assert asyncio.run(_run()) is True


def test_greeting_is_never_blocked_indefinitely():
    """A client that never reports subscription must still get a greeting.
    A greeting that never happens is a worse failure than a clipped one."""
    room = _FakeRoom()

    async def _run():
        loop = asyncio.get_running_loop()
        started = loop.time()
        ready = await agent.wait_for_remote_audio(room, timeout=0.2)
        return ready, loop.time() - started

    ready, elapsed = asyncio.run(_run())
    assert ready is False
    assert 0.15 < elapsed < 0.6, f"bounded wait did not honour its timeout ({elapsed:.3f}s)"


def test_a_video_only_participant_does_not_satisfy_the_audio_wait():
    """Subscribing to video proves nothing about the microphone."""
    from livekit import rtc

    room = _FakeRoom([_FakeParticipant([
        _FakePublication(rtc.TrackKind.KIND_VIDEO, subscribed=True)
    ])])
    assert asyncio.run(agent.wait_for_remote_audio(room, timeout=0.15)) is False


def test_an_unsubscribed_audio_publication_does_not_satisfy_the_wait():
    """Published is not subscribed - the whole distinction this wait exists on."""
    from livekit import rtc

    room = _FakeRoom([_FakeParticipant([
        _FakePublication(rtc.TrackKind.KIND_AUDIO, subscribed=False)
    ])])
    assert asyncio.run(agent.wait_for_remote_audio(room, timeout=0.15)) is False
