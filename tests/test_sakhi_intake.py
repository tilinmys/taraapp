"""The 24-question intake checklist.

Guards the fix for "the guardrail is not strictly adhered to and the agent
hallucinates". The questionnaire used to live only in the system prompt with
nothing tracking position in it - 16 state fields for 24 questions - so the
model re-derived its place from the transcript every turn, and invented
questions when it lost the thread.
"""

import pytest

from sakhi_intake import accepts_assent

from sakhi_domain import SakhiCallState
from sakhi_intake import (
    INTAKE_QUESTIONS,
    QUESTIONS_BY_ID,
    IntakeProgress,
    render_inventory,
)


# --- the questionnaire itself ---------------------------------------------

def test_questionnaire_is_the_full_ordered_flow():
    """24 questions (2026-09-10: raised from 17 rewiring onto the
    "Antenatal Quick Check" PDF's ASK/CHECK sections - chronic disease
    history, medications/allergies, fetal heart rate, lab review, and
    broader symptom bundles, per the plan the user approved).

    BP and the three danger signs were originally NOT in the inventory at all -
    they were driven by imperatives in prompt_summary that fired on every turn,
    which is why they pre-empted question one. They are now ordered members of
    the flow like everything else.
    """
    assert len(INTAKE_QUESTIONS) == 24


def test_question_ids_are_unique_and_ordered():
    """Uniqueness always held and still does. Lexical id ordering does NOT,
    by design, since 2026-09-10: new questions (q18+) were deliberately
    given ids past the original q01-q17 range rather than renumbering
    existing ones (which dozens of tests key on), but several of them belong
    EARLIER in the flow (e.g. q21 is Stage 2, q18-q20 are Stage 4). Flow
    order is governed by `stage` via a stable sort in sakhi_intake.py, not by
    id - so the real invariant next_question() depends on is "stage values
    are monotonically non-decreasing across the tuple", not "ids sort
    lexically". See test_protocol_is_seven_stages_in_order in
    test_anc_protocol.py for that check.
    """
    ids = [q.id for q in INTAKE_QUESTIONS]
    assert len(set(ids)) == 24
    stages = [q.stage for q in INTAKE_QUESTIONS]
    assert stages == sorted(stages), "next_question() walks in stage order; stages must be monotonic"


def test_every_question_carries_a_valid_vad_tag():
    for q in INTAKE_QUESTIONS:
        assert q.vad in ("FAST", "SLOW"), f"{q.id} has VAD {q.vad!r}"


def test_prompt_inventory_is_generated_from_the_same_tuple():
    """One source of truth: a question added here must appear in the prompt."""
    inventory = render_inventory()
    for q in INTAKE_QUESTIONS:
        assert q.text in inventory, f"{q.id} missing from the rendered prompt inventory"
    assert inventory.count("[VAD:") == 24


def test_prompt_embeds_the_generated_inventory():
    from sakhi_prompts import SAKHI_SYSTEM_PROMPT

    assert INTAKE_QUESTIONS[0].text in SAKHI_SYSTEM_PROMPT
    assert INTAKE_QUESTIONS[-1].text in SAKHI_SYSTEM_PROMPT
    assert "{inventory}" not in SAKHI_SYSTEM_PROMPT, "template placeholder not substituted"


# --- progress tracking -----------------------------------------------------

def test_starts_at_the_first_question():
    p = IntakeProgress()
    assert p.next_question().id == "q01"
    assert not p.complete


def test_answer_only_lands_on_the_armed_question():
    """An unrelated remark must not tick off a question nobody asked."""
    p = IntakeProgress()
    assert p.record_answer("she seems tired today") is None, "closed a question with none armed"
    assert p.answered == {}

    p.arm_next()
    assert p.record_answer("Suma, twenty four") == "q01"
    assert p.next_question().id == "q02"


def test_empty_or_whitespace_turns_do_not_advance_the_checklist():
    p = IntakeProgress()
    p.arm_next()
    assert p.record_answer("   ") is None
    assert p.next_question().id == "q01", "a silent turn advanced the intake"


def test_a_disfluent_filler_phrase_does_not_falsely_close_a_slow_question():
    """Confirmed live (2026-09-03): "She's a yeah." (13 chars, no exact
    match in _NON_ANSWERS) closed the real call's "which month of pregnancy
    is she in now?" (q02) - a garbled/disfluent transcript with zero
    clinical content, just long enough to clear the character floor. The
    real answer that followed one second later ("She's in sixth month.")
    had nowhere to land, since the question was already marked answered.
    Reproduced here against q01 (whichever question happens to be armed
    first) - the mechanism being guarded is generic, not q02-specific. This
    is the exact failure this file's own record_answer() docstring names as
    "hallucinating... telling something else" - the wrong (or no) fact
    silently closing the wrong slot, not the LLM inventing anything."""
    p = IntakeProgress()
    p.arm_next()
    assert p.record_answer("She's a yeah.") is None, (
        "a disfluent filler-only phrase falsely closed the question"
    )
    assert p.answered == {}
    # The real answer, arriving right after, must still be able to close it.
    assert p.record_answer("She's in sixth month.") == "q01"


def test_a_connectivity_check_does_not_close_the_pending_question():
    """"hello are you there" (2026-09-08, reported live): long enough to
    clear MIN_ANSWER_CHARS, and none of "hello"/"you"/"there" are filler
    words or an exact _NON_ANSWERS phrase, so it survived the word-strip
    fallback and wrongly closed whatever question was pending - exactly the
    same failure class as "She's a yeah." above, a different wording of it."""
    p = IntakeProgress()
    p.arm_next()
    assert p.record_answer("hello are you there") is None, (
        "a connectivity check falsely closed the question"
    )
    assert p.answered == {}
    assert p.record_answer("can you hear me?") is None
    assert p.answered == {}
    # The real answer must still land once she actually answers.
    assert p.record_answer("Suma, twenty four") == "q01"


def test_theek_hai_is_an_assent_for_a_closed_question_but_not_an_open_one():
    """Romanized Hindi "it's fine/okay" - same acknowledgement role as "ok"/
    "yeah", just untransliterated (neither _ASSENTS nor is_backchannel()'s
    Devanagari-only pattern caught it before this fix). Must behave exactly
    like the existing English/Kannada assents: answers a FAST (closed)
    question, but must not be allowed to close a SLOW (open) one - a filler
    closing an open question is the same "wrong fact, wrong slot" failure
    this file already guards against."""
    fast_q = next(q for q in INTAKE_QUESTIONS if accepts_assent(q))  # a yes/no one
    slow_q = next(q for q in INTAKE_QUESTIONS if q.vad == "SLOW")

    p = IntakeProgress()
    p.pending_id = fast_q.id
    assert p.record_answer("theek hai ji") == fast_q.id

    p = IntakeProgress()
    p.pending_id = slow_q.id
    assert p.record_answer("theek hai") is None, (
        "a filler acknowledgement falsely closed an open question"
    )
    assert p.answered == {}


def test_the_same_question_is_not_closed_twice():
    p = IntakeProgress()
    p.arm_next()
    p.record_answer("Suma, twenty four")
    # Second reply with nothing re-armed must not close q02.
    assert p.record_answer("actually she is twenty five") is None
    assert len(p.answered) == 1


def test_walks_every_question_then_reports_complete():
    p = IntakeProgress()
    seen = []
    for _ in range(len(INTAKE_QUESTIONS)):
        q = p.arm_next()
        seen.append(q.id)
        # A real-shaped reply (yes + a number) that every typed schema accepts.
        # The old placeholder "an answer" is Latin text with no clinical
        # content - exactly what validate_answer now rejects as out of domain.
        p.record_answer("ಹೌದು, 2 ಇದೆ")
    assert seen == [q.id for q in INTAKE_QUESTIONS], "questions were skipped or reordered"
    assert p.complete
    assert p.arm_next() is None
    assert "COMPLETE" in p.summary()


# --- auto-satisfaction from pipeline data ---------------------------------

def test_name_and_age_from_the_record_do_not_get_asked():
    """search_patient already returned them; asking again wastes the call."""
    state = SakhiCallState(name="Suma Gowda", age=24)
    state.intake.sync_from_state(state)
    assert "q01" in state.intake.answered
    assert state.intake.next_question().id == "q02"


def test_name_without_age_still_asks_question_one():
    state = SakhiCallState(name="Suma Gowda")
    state.intake.sync_from_state(state)
    assert state.intake.next_question().id == "q01"


def test_gestational_weeks_satisfies_the_lmp_question():
    state = SakhiCallState(gestational_weeks=28)
    state.intake.sync_from_state(state)
    assert "q02" in state.intake.answered  # LMP/gestation is Stage 2 q02 now


def test_recorded_bp_and_symptoms_satisfy_the_vitals_questions():
    """Only the tools may tick vitals off - not the model claiming it asked."""
    from sakhi_domain import BloodPressure

    state = SakhiCallState(blood_pressure=BloodPressure(118, 76), symptoms_screened=True)
    state.intake.sync_from_state(state)
    assert "q06" in state.intake.answered  # BP is Stage 3 q06 now
    assert "q09" in state.intake.answered  # symptom screen is Stage 4 q09 now


def test_blood_pressure_is_not_the_first_question():
    """The reported bug: Sakhi opened every call by asking for BP.

    Asserted on `satisfied_by`/`id`, not on an English substring in
    `.text`: the 7-stage rewrite (2026-09-02) made q01's actual text
    Kannada ("ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಹೇಳಿ ಅಕ್ಕಾ"), which no longer
    contains the Latin word "name" - the ORIGINAL check silently broke at
    that rewrite and was never caught until a full-suite run surfaced it.
    A script-independent field is the fix, not a translated string that
    the next rewrite would just as quietly re-break.
    """
    first = INTAKE_QUESTIONS[0]
    assert "blood pressure" not in first.text.lower()
    assert first.id == "q01" and first.satisfied_by == "identity"


def test_vitals_come_early_but_after_identification():
    ids = [q.id for q in INTAKE_QUESTIONS]
    bp = ids.index("q04")
    assert 2 <= bp <= 4, (
        "BP must follow patient particulars but must not be buried 20 questions deep"
    )


def test_sync_never_overwrites_a_real_answer():
    state = SakhiCallState(name="Suma", age=24)
    state.intake.arm_next()
    state.intake.record_answer("Suma Gowda, twenty four years")
    state.intake.sync_from_state(state)
    assert state.intake.answered["q01"] == "Suma Gowda, twenty four years"


# --- what the model actually sees ------------------------------------------

def test_prompt_summary_names_the_exact_next_question():
    """The model must be told which question to ask, not asked to work it out."""
    summary = SakhiCallState().prompt_summary()
    assert "intake=0/24" in summary
    assert "NEXT QUESTION" in summary
    assert QUESTIONS_BY_ID["q01"].text in summary


def test_prompt_summary_advances_as_questions_are_answered():
    state = SakhiCallState()
    state.intake.arm_next()
    state.intake.record_answer("Suma, twenty four")
    summary = state.prompt_summary()
    assert "intake=1/24" in summary
    assert QUESTIONS_BY_ID["q02"].text in summary
    assert QUESTIONS_BY_ID["q01"].text not in summary, "re-asking an answered question"


def test_summary_stays_compact_because_it_is_resent_every_turn():
    """The full inventory belongs in the system prompt, not in each turn."""
    state = SakhiCallState()
    assert len(state.intake.summary()) < 300, (
        "per-turn intake block is large enough to cost real prefill latency"
    )
