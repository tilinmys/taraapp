"""Controlled voice-test findings (2026-09-28): state must be deterministic.

Each test names the observed failure. Root causes are traced in the report;
the short version is in each docstring.
"""

import pytest

import agent
from agent import Language, SakhiCallState
from guardrails import GUARDRAILS, classify_turn, medication_requested
from sakhi_domain import BloodPressure, RiskLevel, SymptomSet
from sakhi_intake import INTAKE_QUESTIONS, QUESTIONS_BY_ID
from sakhi_triage import TRIAGE


def _category(text):
    return GUARDRAILS.sanitize_input(text, language="kn").category


# --- PROBLEM 1 / TEST B: a medication REPORT is not an advice request -----

@pytest.mark.parametrize("text", [
    "I take iron and calcium tablets.",
    "I take only iron tablets and calcium tablets. No other medicines.",
    "She takes only iron and calcium tablets, no other medicines",
    "She is not taking any other medicines, only iron and calcium",
    "ಬರೀ ಕಬ್ಬಿಣದ ಮಾತ್ರೆ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ ಮಾತ್ರೆ ತಗೋತಾರೆ. ಬೇರೆ ಯಾವ ಔಷಧಿನೂ ಇಲ್ಲ.",
    "ಕಬ್ಬಿಣದ ಮಾತ್ರೆ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ ಮಾತ್ರೆ ಮಾತ್ರ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದಾರೆ, ಬೇರೆ ಯಾವುದೇ ಮಾತ್ರೆ ಇಲ್ಲ",
    "सिर्फ आयरन और कैल्शियम की गोली लेती है, और कोई दवा नहीं",
])
def test_medication_report_is_recorded_not_refused(text):
    assert _category(text) is None
    assert medication_requested(text) is False


# --- TEST C: a real advice request still gets the approved script ---------

@pytest.mark.parametrize("text", [
    "Should I increase my iron tablet dose?",
    "What dose of iron should I take?",
    "Can she stop the calcium tablets?",
    "Is there any medicine for her fever?",
    "ಕಬ್ಬಿಣದ ಮಾತ್ರೆ ಡೋಸ್ ಜಾಸ್ತಿ ಮಾಡಬೇಕಾ?",
    "ಜ್ವರಕ್ಕೆ ಯಾವ ಮಾತ್ರೆ ಕೊಡಲಿ?",
    "बुखार के लिए कौन सी दवा दूँ?",
])
def test_medication_advice_request_still_refused(text):
    assert _category(text) == "medical_advice"


# --- PROBLEM 5 / TEST F: a DENIED danger sign is not an emergency ---------

@pytest.mark.parametrize("text", [
    "BP is 120 by 80, no severe pain",
    "no difficulty breathing",
    "there is no heavy bleeding",
    "she has not fainted",
    "she did not collapse and there is no chest pain",
])
def test_negated_danger_words_do_not_trigger_the_emergency_script(text):
    assert classify_turn(text) != "emergency"


@pytest.mark.parametrize("text", [
    "she is bleeding heavily",
    "No, she is bleeding heavily",          # the "no" answers Sakhi, then a real sign
    "she fainted and has chest pain",
    "she has severe pain in the stomach",
])
def test_real_emergencies_still_trigger(text):
    assert classify_turn(text) == "emergency"


def test_normal_bp_with_no_danger_signs_is_routine():
    assessment = TRIAGE.assess(BloodPressure(120, 80), SymptomSet())
    assert assessment.level is RiskLevel.ROUTINE
    assert not assessment.requires_escalation


# --- PROBLEM 7 / TESTS D + E: BP must not close without a BP --------------

def _state_with_q06_armed():
    state = SakhiCallState(language=Language.KANNADA)
    for q in INTAKE_QUESTIONS:
        if q.id == "q06":
            break
        state.intake.mark_satisfied(q.id, "fixture")
    state.intake.pending_id = "q06"
    return state


@pytest.mark.parametrize("text", [
    "I am the network protocols right now",   # irrelevant (TEST D)
    "ಹೌದು ಅಕ್ಕ",                              # no reading
    "ಸರಿಯಾಗಿ ಗೊತ್ತಾಗಲಿಲ್ಲ ಇನ್ನೊಮ್ಮೆ",            # unclear (TEST E)
    "140/90",                                 # a reading, but not yet RECORDED
])
def test_bp_question_does_not_close_until_a_bp_is_recorded(text):
    state = _state_with_q06_armed()
    assert agent.SakhiAgent._pending_answer_matches(state, "q06", text, {}) is False
    assert state.blood_pressure is None
    assert state.intake.pending_id == "q06"
    assert state.intake.next_question().id == "q06"   # weight (q07) is not next


def test_bp_not_measured_is_still_an_answer():
    state = _state_with_q06_armed()
    assert agent.SakhiAgent._pending_answer_matches(state, "q06", "ಬಿಪಿ ಮಾಡಿಲ್ಲ", {}) is True


def test_recorded_bp_closes_q06_and_moves_to_weight():
    state = _state_with_q06_armed()
    state.record_blood_pressure(BloodPressure(120, 80))
    state.intake.sync_from_state(state)
    assert "q06" in state.intake.answered
    assert state.intake.next_question().id == "q07"


# --- PROBLEM 6 / TEST G: a locked (emergency) call asks no routine question

def _locked_state():
    state = SakhiCallState(language=Language.KANNADA)
    state.record_symptoms(SymptomSet(vaginal_bleeding=True))
    state.record_assessment(TRIAGE.assess(None, state.symptoms))
    assert state.assessment.level is RiskLevel.HIGH_RISK
    state.lock_intake_on_danger_sign("vaginal_bleeding")
    return state


def test_emergency_lock_closes_the_questionnaire():
    assert agent.questionnaire_open(_locked_state()) is False
    assert agent.questionnaire_open(SakhiCallState()) is True


def test_scripts_after_a_lock_do_not_append_a_routine_question():
    script = "SCRIPT."
    assert agent._then_current_question(script, _locked_state()) == script


def test_didnt_hear_prompt_after_a_lock_does_not_append_a_routine_question(monkeypatch):
    state = _locked_state()
    session = type("S", (), {"agent_state": "listening", "userdata": state})()
    said = []
    monkeypatch.setattr(agent, "_safe_say", lambda s, t: said.append(t))
    agent._make_unheard_speech_handler(session)({"audio_duration_ms": 2000, "gain_db": 0, "reason": "empty"})
    assert len(said) == 1
    for q in INTAKE_QUESTIONS:
        assert agent.question_text(q, state.language) not in said[0]


def test_forced_escalation_line_is_in_the_call_language():
    assert "connecting a specialist" not in agent.escalation_notice(Language.KANNADA)
    assert agent.escalation_notice(Language.HINDI) != agent.escalation_notice(Language.KANNADA)


# --- PROBLEM 2 / 9: tool fallback lines must not ask or close anything ------

def test_tool_fallback_lines_never_ask_a_question_or_close_the_call():
    from sakhi_tools import tool_fallback_line
    closing = QUESTIONS_BY_ID["q17"].text
    for kind in ("bp", "symptoms", "gestational_age"):
        for lang in (Language.KANNADA, Language.HINDI):
            line = tool_fallback_line(kind, lang, systolic=120, diastolic=80, weeks=24)
            assert line and "?" not in line
            assert "ಎಲ್ಲಾ ವಿವರಗಳನ್ನು" not in line and "ಧನ್ಯವಾದ" not in line
            assert closing not in line


# --- PROBLEM 3 / 4: "didn't hear" is decided by the code, not the model ------

def test_accepted_answer_forbids_saying_not_heard():
    rule = agent._reply_format_instruction("accepted")
    assert "WAS recorded" in rule and "Never say you did not hear" in rule


def test_rejected_answer_says_not_caught_and_claims_nothing_recorded():
    rule = agent._reply_format_instruction("not_answered")
    assert "did NOT answer" in rule and "Do NOT say anything was recorded" in rule


def test_bp_heard_tells_the_model_to_record_it():
    assert "record_bp" in agent._reply_format_instruction("value_to_record")


def test_no_rule_offers_the_model_a_free_choice_to_say_not_heard():
    for status in (None, "accepted", "value_to_record"):
        assert "or say you did not hear" not in agent._reply_format_instruction(status)


@pytest.mark.parametrize("text,heard", [
    ("140/90", True), ("ಬಿಪಿ 120 by 80", True), ("140 over 90", True),
    ("I am the network protocols right now", False), ("ಹೌದು ಅಕ್ಕ", False),
])
def test_bp_reading_detector(text, heard):
    assert bool(agent._BP_READING_RE.search(text)) is heard


# --- From the live call of 2026-09-28 (exact transcripts) --------------------

def test_live_medication_answer_is_not_refused():
    text = "ನಾನು ಐರನ್ ಮಾತ್ರೆ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ ಮಾತ್ರೆ ಮಾತ್ರ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದೇನೆ, ಬೇರೆ ಯಾವುದೇ ಮಾತ್ರೆ ಇಲ್ಲ."
    assert _category(text) is None


def test_question_appended_by_a_script_is_armed():
    """10:53: the medication script appended the scan question without arming
    it; her answer hit NO_ARMED_QUESTION and the scan question was asked twice."""
    state = SakhiCallState(language=Language.KANNADA)
    for q in INTAKE_QUESTIONS:
        if q.id == "q13":
            break
        state.intake.mark_satisfied(q.id, "fixture")
    assert state.intake.pending_id is None
    spoken = agent._then_current_question("SCRIPT.", state)
    assert QUESTIONS_BY_ID["q13"].text in spoken
    assert state.intake.pending_id == "q13"
    assert state.intake.record_answer("ಹೌದು, ಹಿಂದಿನ ತಿಂಗಳು ಸ್ಕ್ಯಾನ್ ಮಾಡಿದ್ದೇವೆ") == "q13"
    assert state.intake.next_question().id != "q13"


@pytest.mark.parametrize("qid", ["q10", "q18"])
def test_bare_ella_is_not_an_answer_to_a_danger_question(qid):
    from sakhi_intake import IntakeProgress
    q = QUESTIONS_BY_ID[qid]
    assert IntakeProgress().validate_answer("ಎಲ್ಲಾ", vad=q.vad, question_id=qid)[0] is False
    assert IntakeProgress().validate_answer("ಎಲ್ಲಾ ಸರಿ ಇದೆ", vad=q.vad, question_id=qid)[0] is True
