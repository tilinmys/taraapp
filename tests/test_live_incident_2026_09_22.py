"""Regression coverage for the 2026-09-22 Priya Sharma production call.

These tests assert typed state, not merely that a sequential question id was
closed.  The earlier regression simulated "second pregnancy" closing the
gestational-age slot and therefore passed while reproducing the defect.
"""

from types import SimpleNamespace

import pytest

from agent import SakhiAgent
from clinical_terms import canonicalise
from sakhi_domain import (
    SakhiCallState,
    detect_gestational_age,
    detect_patient_demographics,
    detect_pregnancy_number,
)


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("ippattaidu", 25),
        ("ippattaaru", 26),
        ("ippattelu", 27),
        ("ippatentu", 28),
        ("ippattu entu", 28),
        ("ippattombattu", 29),
        ("ಇಪ್ಪತ್ತೆಂಟು", 28),
        ("28", 28),
    ],
)
def test_neighboring_age_words_do_not_collapse(spoken, expected):
    normalized = canonicalise(spoken)
    assert normalized == str(expected)


def test_exact_incident_demographics_extracts_28_not_25():
    raw = "Taayi hesaru Priya Sharma, avara vayasu ippatentu varsha."
    normalized = canonicalise(raw)
    assert "28" in normalized
    assert detect_patient_demographics(normalized) == ("Priya Sharma", 28)


def test_volunteered_multi_field_turn_populates_typed_state_and_skips_slots():
    state = SakhiCallState(session_id="stable-conversation")
    agent = object.__new__(SakhiAgent)
    agent._toolset = None

    text = canonicalise(
        "Priya Sharma, age ippatentu varsha, second pregnancy, seven months."
    )
    captured = agent._capture_incident_fields(state, text)
    state.intake.sync_from_state(state)

    assert captured == {
        "name": "[REDACTED]",
        "age": 28,
        "gestational_weeks": 28,
        "pregnancy_number": 2,
    }
    assert state.name == "Priya Sharma"
    assert state.age == 28
    assert state.gestational_weeks == 28
    assert state.anc_checkpoints["gravida"]["value"] == "2"
    assert {"q01", "q02", "q04"} <= set(state.intake.answered)
    assert state.intake.next_question().id not in {"q01", "q02", "q04"}


def test_cross_field_answers_do_not_close_the_armed_question():
    state = SakhiCallState(name="Priya Sharma", age=28, name_confirmed=True)
    state.intake.sync_from_state(state)
    assert state.intake.arm_next().id == "q02"

    pregnancy_text = canonicalise("Eradaneya garbha dharane")
    captured = {"pregnancy_number": detect_pregnancy_number(pregnancy_text)}
    state.record_anc_checkpoint(2, "gravida", "2")
    state.intake.sync_from_state(state)

    assert not SakhiAgent._pending_answer_matches(
        state, "q02", pregnancy_text, captured
    )
    assert "q02" not in state.intake.answered
    assert "q04" in state.intake.answered
    assert state.intake.pending_id == "q02"

    month_text = canonicalise("Yelu tingalu, akka")
    weeks = detect_gestational_age(month_text)
    state.identify(gestational_weeks=weeks)
    state.intake.sync_from_state(state)
    assert weeks == 28
    assert "q02" in state.intake.answered
    assert state.intake.next_question().id not in {"q01", "q02", "q04"}


def test_confirmed_identity_survives_noisy_later_transcript():
    state = SakhiCallState()
    state.identify(name="Priya Sharma", age=28)
    state.identify(name="Pranasharma", age=25)
    assert state.name == "Priya Sharma"
    assert state.age == 28
    assert state.name_confirmed is True


def test_final_turn_identity_uses_message_id_not_equal_text():
    first = SimpleNamespace(id="utterance-1")
    second = SimpleNamespace(id="utterance-2")
    assert SakhiAgent._turn_id(first, "illa") == "utterance-1"
    assert SakhiAgent._turn_id(second, "illa") == "utterance-2"
    assert SakhiAgent._turn_id(first, "different callback text") == "utterance-1"


def test_temporary_stt_failure_never_resets_progress():
    state = SakhiCallState(session_id="stable-conversation")
    state.identify(name="Priya Sharma", age=28, gestational_weeks=28)
    state.record_anc_checkpoint(2, "gravida", "2")
    state.intake.sync_from_state(state)
    before = dict(state.intake.answered)

    # An empty/failed final cannot close, clear, or rewind anything.
    state.intake.record_answer("")
    state.intake.sync_from_state(state)
    assert state.session_id == "stable-conversation"
    assert state.intake.answered == before
    assert state.name == "Priya Sharma"
    assert state.age == 28
    assert state.gestational_weeks == 28
