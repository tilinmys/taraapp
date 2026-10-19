"""Forensic audit remediation (2026-09-25): the reported production failures.

Two cases assert the behaviour that was actually shipped rather than the
original ticket wording, on purpose (see each test's docstring):
  * #4 - an irrelevant answer is a rejection that keeps the question armed,
    not a NEEDS_CLARIFICATION return value;
  * #5 - "ಮೊದಲೇ ಹೇರಿಗೆ ಆಗಿದೆ" carries no count, so no number is invented.
"""

from unittest.mock import MagicMock

import pytest

import agent
from guardrails import medication_requested
from sakhi_domain import (
    Language,
    RiskLevel,
    SakhiCallState,
    detect_pregnancy_number,
    detect_symptoms,
)
from sakhi_intake import INTAKE_QUESTIONS, QUESTIONS_BY_ID, IntakeProgress
from sakhi_triage import TriageEngine


# 1. Turn 22 - "fits: never diagnosed" raised a false eclampsia escalation.
def test_kannada_fits_negation_not_escalated():
    text = ("ಇಲ್ಲಕ್ಕ, ಇವು ಯಾವುದು ಇಲ್ಲ. ಮೊದಲಿನಿಂದಲೂ ಡಯಾಬಿಟ್, ಬಿಪಿ, ಥೈರಾಯ್ಡ್, "
            "ಫಿಟ್ಸ್ ಯಾವುದು ಡಯಾಗ್ನೋಸ್ ಆಗಿಲ್ಲ.")
    symptoms = detect_symptoms(text)
    assert symptoms.convulsions is False
    assessment = TriageEngine().assess(None, symptoms)
    assert assessment.level is not RiskLevel.HIGH_RISK


# 2. Turns 14 & 25 - a Td dose already received was refused as medical advice.
def test_indic_medication_factual_answer_allowed():
    assert medication_requested("ಎರಡು ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು ಪಡೆದಿದ್ದೇನೆ.") is False


# 3. ...while a real "should I take this tablet?" stays guarded.
def test_indic_medication_advice_request_blocked():
    assert medication_requested("ನಾನು ಈ ಮಾತ್ರೆ ತಗೋಬೇಕಾ?") is True


# 4. Turn 6 - "I am the network protocols right now" closed a question.
def test_irrelevant_english_sentence_rejected():
    """The rejection lives in validate_answer(), run by record_answer(), so
    the stall-escape still bounds how often the question is re-asked.
    _pending_answer_matches() returning False instead would skip
    record_answer() and re-ask q05 for the rest of the call - so there is no
    NEEDS_CLARIFICATION value; "needs clarification" = the question stays
    armed."""
    text = "I am the network protocols right now"
    q05 = QUESTIONS_BY_ID["q05"]
    valid, reason = IntakeProgress().validate_answer(text, vad=q05.vad, question_id="q05")
    assert (valid, reason) == (False, "out_of_domain")

    progress = IntakeProgress()
    progress.pending_id = "q05"
    assert progress.record_answer(text) is None      # not advanced
    assert progress.pending_id == "q05"              # still armed: needs clarification
    assert "q05" not in progress.answered


# 5. q04 repeated because Kannada-script delivery words were unknown.
def test_gravida_native_kannada_parity_extracted():
    """Kannada script is now understood ("ಎರಡನೇ ಹೇರಿಗೆ" -> 2, "ಇದು ಮೊದಲ ಹೆರಿಗೆ"
    -> 1). "ಮೊದಲೇ ಹೇರಿಗೆ ಆಗಿದೆ" means "already delivered before" - ಮೊದಲೇ is
    "already", not "first" - so it states no count and must not become 1:
    a wrong gravida/parity is worse than asking "how many?"."""
    assert detect_pregnancy_number("ಎರಡನೇ ಹೇರಿಗೆ") == 2
    assert detect_pregnancy_number("ಇದು ಮೊದಲ ಹೆರಿಗೆ") == 1
    assert detect_pregnancy_number("ಮೂರನೇ ಗೃಹ್ಮಧಾರಣೆ") == 3     # STT spelling
    assert detect_pregnancy_number("ಮೊದಲೇ ಹೇರಿಗೆ ಆಗಿದೆ") is None


# 6. Turn 30 - after q17 the call stayed open and courtesy turns looped.
def _completed_agent():
    state = SakhiCallState(language=Language.KANNADA)
    state.identify(name="Anjali", age=24)
    for question in INTAKE_QUESTIONS:
        state.intake.mark_satisfied(question.id, "answered")
    assert state.intake.complete
    sakhi = agent.SakhiAgent(instructions="test", chat_ctx=agent.llm.ChatContext())
    session = MagicMock()
    session.userdata = state
    sakhi._activity = MagicMock(session=session)
    return sakhi, session, state


def _message(text):
    message = MagicMock()
    message.text_content = text
    message.content = [text]
    return message


@pytest.mark.asyncio
async def test_session_completed_no_q01_loop(monkeypatch):
    hangups = []
    monkeypatch.setattr(agent, "schedule_hangup_after_playout", lambda s: hangups.append(s))
    sakhi, session, state = _completed_agent()

    try:
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("ತುಂಬಾ ಧನ್ಯವಾದಗಳು"))
    except agent.StopResponse:
        pass

    # Nothing re-armed and q01 was not asked again.
    assert state.intake.pending_id is None
    assert state.intake.arm_next() is None
    q01_text = QUESTIONS_BY_ID["q01"].text
    for call in session.say.call_args_list:
        assert q01_text not in str(call.args[0] if call.args else "")

    # Sakhi's thank-you now ends the call (terminal farewell hangup).
    sakhi._user_has_spoken = True
    sakhi._maybe_schedule_farewell_hangup("ಧನ್ಯವಾದಗಳು ಅಕ್ಕಾ.")
    assert len(hangups) == 1
    assert sakhi._farewell_hangup_scheduled
