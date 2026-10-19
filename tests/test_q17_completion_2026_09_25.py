"""Clean call end after the q17 closing statement (2026-09-25, Turn 30)."""

import pytest

import agent
from sakhi_domain import SakhiCallState
from sakhi_intake import INTAKE_QUESTIONS, QUESTIONS_BY_ID
from agent import Language


class _FakeAgent:
    _farewell_hangup_scheduled = False
    _user_has_spoken = True
    session = None
    _maybe_schedule_farewell_hangup = agent.SakhiAgent._maybe_schedule_farewell_hangup
    _state = agent.SakhiAgent._state


def _fake(state):
    fake = _FakeAgent()
    fake.session = type("Session", (), {"userdata": state})()
    return fake


def _at_q17():
    state = SakhiCallState()
    for q in INTAKE_QUESTIONS:
        if q.id != "q17":
            state.intake.mark_satisfied(q.id, "answered")
    state.intake.arm_next()
    assert state.intake.pending_id == "q17"
    return state


@pytest.fixture
def hangups(monkeypatch):
    called = []
    monkeypatch.setattr(agent, "schedule_hangup_after_playout", lambda s: called.append(s))
    return called


@pytest.mark.parametrize("lang,attr", [(Language.KANNADA, "text"), (Language.HINDI, "text_hi")])
def test_speaking_the_q17_close_completes_intake_and_hangs_up(hangups, lang, attr):
    state = _at_q17()
    closing = "ಸರಿ ಅಕ್ಕಾ. " + getattr(QUESTIONS_BY_ID["q17"], attr)
    _fake(state)._maybe_schedule_farewell_hangup(closing)
    assert len(hangups) == 1
    assert state.intake.complete
    assert state.intake.pending_id is None
    assert "q17" in state.intake.answered


def test_thank_you_after_completion_ends_the_call(hangups):
    state = SakhiCallState()
    for q in INTAKE_QUESTIONS:
        state.intake.mark_satisfied(q.id, "answered")
    _fake(state)._maybe_schedule_farewell_hangup("ಧನ್ಯವಾದಗಳು ಅಕ್ಕಾ.")
    assert len(hangups) == 1


@pytest.mark.parametrize("line", [
    "ಧನ್ಯವಾದಗಳು ಅಕ್ಕಾರ. ಈಗ ರಕ್ತದೊತ್ತಡ ಹೇಳಿ.",
    "धन्यवाद दीदी। अब रक्तचाप बताइए।",
])
def test_thank_you_mid_screening_never_ends_the_call(hangups, line):
    state = SakhiCallState()
    state.intake.arm_next()
    _fake(state)._maybe_schedule_farewell_hangup(line)
    assert hangups == []


def test_hallucinated_close_at_q01_does_not_end_the_call(hangups):
    """Seen in a live-LLM run: the model said the q17 close with q01 armed."""
    state = SakhiCallState()
    state.intake.arm_next()
    _fake(state)._maybe_schedule_farewell_hangup(
        "ಎಲ್ಲಾ ವಿವರಗಳನ್ನು ಯಶಸ್ವಿಯಾಗಿ ದಾಖಲಿಸಲಾಗಿದೆ. ಧನ್ಯವಾದಗಳು ಅಕ್ಕಾ.")
    assert hangups == []
    assert "q17" not in state.intake.answered
