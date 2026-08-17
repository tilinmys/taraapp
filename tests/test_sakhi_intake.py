"""The 24-question intake checklist.

Guards the fix for "the guardrail is not strictly adhered to and the agent
hallucinates". The questionnaire used to live only in the system prompt with
nothing tracking position in it - 16 state fields for 24 questions - so the
model re-derived its place from the transcript every turn, and invented
questions when it lost the thread.
"""

import pytest

from sakhi_domain import SakhiCallState
from sakhi_intake import (
    INTAKE_QUESTIONS,
    QUESTIONS_BY_ID,
    IntakeProgress,
    render_inventory,
)


# --- the questionnaire itself ---------------------------------------------

def test_questionnaire_is_the_full_ordered_flow():
    """24 history questions + 2 vitals.

    BP and the three danger signs were originally NOT in the inventory at all -
    they were driven by imperatives in prompt_summary that fired on every turn,
    which is why they pre-empted question one. They are now ordered members of
    the flow like everything else.
    """
    assert len(INTAKE_QUESTIONS) == 26


def test_question_ids_are_unique_and_ordered():
    ids = [q.id for q in INTAKE_QUESTIONS]
    assert len(set(ids)) == 26
    assert ids == sorted(ids), "next_question() walks in order; ids must sort in flow order"


def test_every_question_carries_a_valid_vad_tag():
    for q in INTAKE_QUESTIONS:
        assert q.vad in ("FAST", "SLOW"), f"{q.id} has VAD {q.vad!r}"


def test_prompt_inventory_is_generated_from_the_same_tuple():
    """One source of truth: a question added here must appear in the prompt."""
    inventory = render_inventory()
    for q in INTAKE_QUESTIONS:
        assert q.text in inventory, f"{q.id} missing from the rendered prompt inventory"
    assert inventory.count("[VAD:") == 26


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
        p.record_answer("an answer")
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
    assert "q06" in state.intake.answered


def test_recorded_bp_and_symptoms_satisfy_the_vitals_questions():
    """Only the tools may tick vitals off - not the model claiming it asked."""
    from sakhi_domain import BloodPressure

    state = SakhiCallState(blood_pressure=BloodPressure(118, 76), symptoms_screened=True)
    state.intake.sync_from_state(state)
    assert "q04" in state.intake.answered
    assert "q05" in state.intake.answered


def test_blood_pressure_is_not_the_first_question():
    """The reported bug: Sakhi opened every call by asking for BP."""
    first = INTAKE_QUESTIONS[0]
    assert "blood pressure" not in first.text.lower()
    assert first.id == "q01" and "name" in first.text.lower()


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
    assert "intake=0/26" in summary
    assert "NEXT QUESTION" in summary
    assert QUESTIONS_BY_ID["q01"].text in summary


def test_prompt_summary_advances_as_questions_are_answered():
    state = SakhiCallState()
    state.intake.arm_next()
    state.intake.record_answer("Suma, twenty four")
    summary = state.prompt_summary()
    assert "intake=1/26" in summary
    assert QUESTIONS_BY_ID["q02"].text in summary
    assert QUESTIONS_BY_ID["q01"].text not in summary, "re-asking an answered question"


def test_summary_stays_compact_because_it_is_resent_every_turn():
    """The full inventory belongs in the system prompt, not in each turn."""
    state = SakhiCallState()
    assert len(state.intake.summary()) < 300, (
        "per-turn intake block is large enough to cost real prefill latency"
    )
