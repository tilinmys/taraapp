"""A turn spoken during identity resolution must not answer a later question.

Reproduced from a real call: STT heard "Anjaneya", then "Anjali" - the lookup
failed, Sakhi asked her to spell it, and the spelled-out reply ("Anjali A N J
A L I") plus a stray "it's her name" both landed as the RECORDED ANSWER to
q03 ("when did her symptoms first start"). Reported live as Sakhi
"hallucinating... telling something else" - the mechanism was never invented
facts, it was the right fact landing in the wrong slot: the intake tracker had
no concept of "we are mid-identity-resolution, nothing here counts toward the
numbered questionnaire".

Fix: while a question after q01 is armed and identity is still unresolved
(state.is_identified is False), on_user_turn_completed skips
intake.record_answer() entirely. is_identified covers both the real-lookup
path and the Guest-Patient ceiling-fallback path (both go through
state.identify()), so a genuinely resolved identity - including the
placeholder one - unblocks intake progress again exactly as before.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

import agent
from sakhi_domain import SakhiCallState
from sakhi_intake import QUESTIONS_BY_ID


def _run(coro):
    return asyncio.run(coro)


def _agent_with_state(state):
    sakhi = agent.SakhiAgent(instructions="t", chat_ctx=agent.llm.ChatContext())
    session = MagicMock()
    session.userdata = state
    sakhi._activity = MagicMock(session=session)
    return sakhi


def _turn(sakhi, text):
    msg = MagicMock()
    msg.text_content = text
    ctx = MagicMock(items=[])
    try:
        _run(sakhi.on_user_turn_completed(ctx, msg))
    except agent.StopResponse:
        pass


def test_unresolved_identity_blocks_recording_a_later_question():
    """The exact reproduction: q03 armed, identity never confirmed, a spelled
    -out name arrives - it must not close q03."""
    state = SakhiCallState()
    state.intake.pending_id = "q03"  # simulate: q01/q02 already passed
    sakhi = _agent_with_state(state)

    _turn(sakhi, "Anjali A N J A L I")

    assert "q03" not in state.intake.answered, (
        "a spelling clarification was recorded as the answer to an unrelated "
        "clinical question"
    )
    assert state.intake.pending_id == "q03", "q03 must still be pending, not skipped"


def test_q01_itself_is_never_blocked_by_the_gate():
    """q01 IS the identity question - the gate must not create a deadlock
    where identity can never be established in the first place."""
    state = SakhiCallState()
    state.intake.pending_id = "q01"
    sakhi = _agent_with_state(state)

    _turn(sakhi, "her name is Suma Gowda, twenty four")

    assert "q01" in state.intake.answered, "q01 must still be answerable pre-identity"


def test_recording_resumes_once_identity_is_confirmed():
    state = SakhiCallState(patient_id="M-201", name="Suma Gowda", name_confirmed=True)
    state.intake.pending_id = "q03"
    sakhi = _agent_with_state(state)

    _turn(sakhi, "she has had a headache since Monday")

    assert "q03" in state.intake.answered, (
        "a real answer was blocked even though identity was already resolved"
    )


def test_guest_patient_fallback_also_unblocks_the_gate():
    """The ceiling-fallback path confirms name WITHOUT a real patient_id -
    is_identified must recognise that too, or the fallback deadlocks intake."""
    state = SakhiCallState(name="Guest Patient", name_confirmed=True)
    assert state.is_identified
    state.intake.pending_id = "q03"
    sakhi = _agent_with_state(state)

    _turn(sakhi, "she has had a headache since Monday")

    assert "q03" in state.intake.answered


def test_awaiting_identity_box_also_blocks_recording():
    """A stray spoken turn while the frontend name/age box is up ("ok, typing
    now") must not be recorded as the answer to whatever question happens to
    be armed - she's been told to type it, not say anything. Distinct from
    the is_identified gate above: this covers the box-armed window
    specifically, including cases where identity was never even attempted
    through voice for the currently-armed question."""
    state = SakhiCallState()
    state.awaiting_identity_box = True
    state.intake.pending_id = "q03"
    sakhi = _agent_with_state(state)

    _turn(sakhi, "ok, typing now")

    assert "q03" not in state.intake.answered
    assert state.intake.pending_id == "q03"


def test_no_answer_is_lost_the_question_just_stays_armed():
    """Suppressing the bad recording must not silently drop a question - it
    stays pending and gets asked again cleanly once identity resolves.
    (next_question() is deliberately not asserted here: it reports the first
    UNANSWERED question by inventory order, which in this minimal setup is
    q01 - a fact about this test's setup, not about the gate. pending_id is
    the thing record_answer() would have cleared had it wrongly fired, so
    it's the correct, direct proof nothing was silently consumed.)"""
    state = SakhiCallState()
    state.intake.pending_id = "q03"
    sakhi = _agent_with_state(state)

    _turn(sakhi, "It's her name.")

    assert state.intake.pending_id == "q03", "record_answer cleared pending_id despite the gate"
    assert "q03" not in state.intake.answered
