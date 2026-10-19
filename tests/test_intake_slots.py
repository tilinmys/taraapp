"""Fixes the reported "trapped on q02, repeatedly asks how many months
pregnant" loop.

NAMING NOTE: this repo has no "clinical slot extraction module" - confirmed
directly, there is no such file anywhere in the codebase. Gestational age
has always been captured only via the model calling
`ClinicalToolset.record_gestational_age(weeks: int)`, with the months-to-
weeks conversion left entirely to the model's own arithmetic
(sakhi_prompts.py: "'third month' means gestational_weeks=12"). This file is
named test_intake_slots.py to match what was asked, but tests the real fix:
`sakhi_domain.detect_gestational_age()`, a deterministic safety net mirroring
the already-proven `detect_symptoms()` pattern.

REAL ROOT CAUSE, verified directly against the code rather than assumed:
q02 is a FAST question, and IntakeProgress.record_answer()'s substantive
check has no trouble with "five months", "5th month", or "ಐದು ತಿಂಗಳು" - all
three were confirmed to close q02 normally before this file was written. The
actual gap is that `state.gestational_weeks` - the typed clinical fact,
separate from the intake checklist - never got set unless the model both (a)
called record_gestational_age at all, and (b) did the months-to-weeks
arithmetic correctly, with no code-owned fallback if either failed. Combined
with the identity_pending gate (agent.py, a real and independently-tested
fix for a different bug - see test_identity_gate.py) silently dropping ANY
non-q01 answer while identity is unresolved, a model that was slow to call
register_mother could leave q02 re-armed indefinitely - which is what
"repeatedly asks the same question" looks like from the outside.
detect_gestational_age(), called unconditionally (not gated by
identity_pending, the same way detect_symptoms() already isn't), closes
this: the moment gestational age is heard in speech,
IntakeProgress.sync_from_state() auto-satisfies q02 on the very next
prompt_summary(), regardless of what the model does or doesn't call.
"""

import asyncio
import tempfile
import types
from pathlib import Path

import pytest

from sakhi_domain import SakhiCallState, detect_gestational_age
from sakhi_intake import INTAKE_QUESTIONS
from sakhi_sql_repository import SQLitePatientRepository
from sakhi_tools import ClinicalToolset
from sakhi_triage import TRIAGE


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def toolset():
    repo = _run(SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load())
    return ClinicalToolset(repo, TRIAGE)


def _ctx(state):
    return types.SimpleNamespace(userdata=state)


# --------------------------------------------------------------------------
# detect_gestational_age(): the deterministic months -> weeks mapping
# --------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,weeks", [
    ("first month", 4), ("1st month", 4),
    ("second month", 8), ("2nd month", 8),
    ("third month", 12), ("3rd month", 12),
    ("fourth month", 16), ("4th month", 16),
    ("five months", 20), ("fifth month", 20), ("5th month", 20), ("5 months", 20),
    ("six months", 24), ("6th month", 24),
    ("seven months", 28), ("7th month", 28),
    ("eight months", 32), ("8th month", 32),
    ("nine months", 36), ("9th month", 36),
])
def test_month_expression_maps_to_the_right_week_count(phrase, weeks):
    assert detect_gestational_age(phrase) == weeks


@pytest.mark.parametrize("phrase,weeks", [
    ("ಐದು ತಿಂಗಳು", 20),
    ("ಒಂದು ತಿಂಗಳು", 4),
    ("ಒಂಬತ್ತು ತಿಂಗಳು ಆಗಿದೆ", 36),
    ("पांच महीने", 20),
    ("पाँच महीने", 20),
    ("तीसरा महीना", 12),
    ("चौथा महीना है", 16),
])
def test_indic_month_expression_maps_to_the_right_week_count(phrase, weeks):
    """The exact failure mode confirmed and fixed while building this:
    Python's `re` module treats a trailing Kannada/Hindi combining vowel
    sign as \\W, not \\w - so both a \\b-anchored regex AND \\w-based word
    tokenization silently mishandle "ಐದು"/"पांच". detect_gestational_age()
    matches these via plain substring search instead, the same technique
    detect_symptoms() already relies on for its own Kannada/Hindi patterns."""
    assert detect_gestational_age(phrase) == weeks


def test_no_month_mention_returns_none():
    assert detect_gestational_age("she is doing fine") is None
    assert detect_gestational_age("") is None
    assert detect_gestational_age(None) is None


def test_out_of_range_month_is_rejected():
    """A stray "twelve months" (not a valid pregnancy duration) must not be
    silently accepted as month=1 via prefix-matching or similar."""
    assert detect_gestational_age("twelve months") is None


# --------------------------------------------------------------------------
# End to end: the turn that used to loop
# --------------------------------------------------------------------------

def test_five_months_answers_q02_without_looping():
    """The exact scenario reported: q02 armed, ASHA says "five months"."""
    state = SakhiCallState()
    state.intake.arm_next()
    assert state.intake.pending_id == "q01"
    state.intake.record_answer("Suma, twenty four")
    state.identify(name="Suma", age=24)

    armed = state.intake.arm_next()
    assert armed.id == "q02"

    # The deterministic safety net a real turn would go through
    # (SakhiAgent._capture_gestational_age, agent.py) - called here directly
    # so this test exercises the real function with no LiveKit session
    # needed, matching this test suite's established pattern for
    # _capture_symptoms-style logic.
    weeks = detect_gestational_age("five months")
    assert weeks == 20
    state.identify(gestational_weeks=weeks)

    closed = state.intake.record_answer("five months")
    assert closed == "q02", "q02 must close on this turn, not stay armed"

    # q03 (last-period date) no longer applies once the months are known.
    state.intake.sync_applicability(state)
    assert state.intake.answered.get("q03") == "(not applicable)"

    next_q = state.intake.arm_next()
    # q04, not q03: the last-period question no longer applies once months
    # are known (it re-asked what q02 had just covered).
    assert next_q.id == "q04", "must move on, not re-ask q02 or its q03 duplicate"
    assert state.gestational_weeks == 20


def test_gestational_age_heard_in_speech_unblocks_q02_even_if_identity_is_unresolved():
    """The actual root cause this closes: the identity_pending gate
    (agent.py) correctly refuses to let ANY non-q01 spoken turn close a
    question while identity is unresolved - a real, separately-tested fix
    for a different bug (test_identity_gate.py). Without a deterministic
    safety net, an unresolved identity leaves gestational_weeks unset
    indefinitely, and q02 (armed via the checklist) never gets ticked off
    either, since the model sees "NEXT QUESTION: q02" every single turn - a
    real repetition loop, just not caused by the "slot extractor" the
    original report assumed.

    detect_gestational_age(), unlike intake.record_answer(), is NOT gated on
    identity - the same way detect_symptoms() isn't - so sync_from_state()
    can auto-satisfy q02 regardless of whether identity has resolved yet.
    """
    state = SakhiCallState()  # is_identified is False: no patient_id, no confirmed name
    # Isolate q02 specifically: q01 already answered (by id, not via identify()
    # - deliberately keeping is_identified False), so next_question() would
    # otherwise sit on q02 next.
    state.intake.mark_satisfied("q01", "Suma, 24")
    assert not state.is_identified
    assert state.intake.next_question().id == "q02"

    weeks = detect_gestational_age("ಐದು ತಿಂಗಳು")
    assert weeks == 20
    state.identify(gestational_weeks=weeks)  # what _capture_gestational_age does

    # sync_from_state() (called by prompt_summary()) must tick off q02 from
    # gestational_weeks alone, with no dependency on identity or on
    # record_answer() ever having been allowed to run.
    state.prompt_summary()
    assert "q02" in state.intake.answered
    assert state.intake.next_question().id != "q02", (
        "q02 must not still be the next question once gestational_weeks is known"
    )


def test_a_value_already_recorded_is_never_overwritten_by_the_detector():
    """The protection lives in _capture_gestational_age's own guard
    (agent.py), not in SakhiCallState.identify() - confirmed directly that
    identify() does NOT check the existing value before writing
    gestational_weeks (unlike what its docstring claims for name), so a
    naive `state.identify(gestational_weeks=detect_gestational_age(text))`
    call site would happily clobber an already-recorded reading with a
    later, unrelated month mention. _capture_gestational_age checks
    `state.gestational_weeks` truthy and returns early BEFORE calling
    identify() at all - that check is what this test exercises, via the
    real agent method, not a hand-rolled substitute for it."""
    import agent
    from unittest.mock import MagicMock

    state = SakhiCallState(gestational_weeks=28)
    sakhi = agent.SakhiAgent(instructions="t", chat_ctx=agent.llm.ChatContext())
    session = MagicMock()
    session.userdata = state
    sakhi._activity = MagicMock(session=session)

    sakhi._capture_gestational_age(state, "second month")  # would be 8 weeks if applied
    assert state.gestational_weeks == 28, "a real recorded value must not be overwritten"


def test_capture_gestational_age_sets_the_value_from_raw_speech():
    """The real, full method (agent.py), exercised end to end - not the
    detector function alone."""
    import agent
    from unittest.mock import MagicMock

    state = SakhiCallState()
    sakhi = agent.SakhiAgent(instructions="t", chat_ctx=agent.llm.ChatContext())
    session = MagicMock()
    session.userdata = state
    sakhi._activity = MagicMock(session=session)

    sakhi._capture_gestational_age(state, "ಐದು ತಿಂಗಳು")
    assert state.gestational_weeks == 20


def test_record_gestational_age_tool_still_works_directly(toolset):
    """The tool call path (the model correctly doing the arithmetic itself)
    is unchanged by this fix - both paths OR toward the same state field."""
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.record_gestational_age(_ctx(state), weeks=20)
        assert state.gestational_weeks == 20
    _run(go())
