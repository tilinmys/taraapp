"""Regressions for defects found by scripts/live_llm_scenarios.py (real LLM
chain, 2026-09-24) and the production pipeline log of 2026-09-22.

Each test names the observed behaviour it pins.
"""

import pytest

import agent
from clinical_terms import canonicalise
from sakhi_domain import Language, SakhiCallState, detect_gestational_age
from sakhi_intake import INTAKE_QUESTIONS, QUESTIONS_BY_ID, accepts_assent
from sakhi_prompts import SAKHI_SYSTEM_PROMPT, SAKHI_SYSTEM_PROMPT_EN, SAKHI_SYSTEM_PROMPT_HI, system_prompt_for


# --- prompt: no English lines to parrot, no wait-for-yes loop -------------

@pytest.mark.parametrize("prompt", [
    SAKHI_SYSTEM_PROMPT, SAKHI_SYSTEM_PROMPT_HI, SAKHI_SYSTEM_PROMPT_EN,
    system_prompt_for(Language.KANNADA, stage=1, stage_desc="x", stage_q="q"),
    system_prompt_for(Language.HINDI, stage=3, stage_desc="x", stage_q="q"),
])
def test_prompt_has_no_quoted_english_script_or_confirmation_loop(prompt):
    """Seen: "I heard ಲಕ್ಷ್ಮಿ, age 24 years. Is that correct?" on a Kannada
    call, repeated every turn until she said yes."""
    assert "I heard" not in prompt
    assert "did not catch that clearly" not in prompt
    assert "Only call record_patient_intake after confirmation" not in prompt


def test_prompt_carries_one_question_order_only():
    """The hand-typed "5 BLOCKS" list ordered questions differently from the
    generated checklist the tracker walks."""
    assert "BLOCK 1" not in SAKHI_SYSTEM_PROMPT
    assert "5 BLOCKS" not in SAKHI_SYSTEM_PROMPT


# --- numbers: BP must reach the model intact --------------------------------

@pytest.mark.parametrize("spoken, expected", [
    ("ಬಿಪಿ ನೂರ ಇಪ್ಪತ್ತು ಬೈ ಎಂಬತ್ತು", "120/80"),
    ("ಬಿಪಿ ನೂರ ಹತ್ತು ಬೈ ಎಪ್ಪತ್ತು", "110/70"),
    ("ಬಿಪಿ ಒಂದು ನೂರ ಎಪ್ಪತ್ತು ಬೈ ಒಂದು ನೂರ ಹತ್ತು", "170/110"),
    ("ಬಿಪಿ ನೂರ ನಲವತ್ತು ಬೈ ತೊಂಬತ್ತೈದು", "140/95"),
    ("ಬಿಪಿ ನೂರು ಬೈ ಅರವತ್ತು", "100/60"),
])
def test_kannada_bp_words_canonicalise_to_the_right_reading(spoken, expected):
    """Seen: "ನೂರ ಇಪ್ಪತ್ತು ಬೈ ಎಂಬತ್ತು" became "ನೂರ 20 ಬೈ 80"."""
    assert expected in canonicalise(spoken)


def test_joined_forty_to_ninety_nine_are_numbers():
    assert canonicalise("ಐವತ್ತೆರಡು ಕೆಜಿ") == "52 ಕೆಜಿ"
    assert "85" in canonicalise("ಎಂಬತ್ತೈದು")


def test_two_separate_facts_are_not_merged_into_one_number():
    """"…by ninety, five months" must stay two facts."""
    out = canonicalise("ತೊಂಬತ್ತು ಐದು ತಿಂಗಳು")
    assert "95" not in out
    assert detect_gestational_age(out) == 20


def test_hundreds_word_inside_another_word_is_left_alone():
    assert canonicalise("ನೂರಾರು ಜನ") == "ನೂರಾರು ಜನ"


# --- gestation in a multi-fact sentence -------------------------------------

def test_months_are_read_from_the_number_next_to_them():
    """Seen: "22 ವರ್ಷ, 7 ತಿಂಗಳು" read 22, failed, and months was asked again."""
    text = canonicalise("ಹೆಸರು ಸುಮಾ, ಇಪ್ಪತ್ತೆರಡು ವರ್ಷ, ಏಳು ತಿಂಗಳು, ಬಿಪಿ ನೂರ ಇಪ್ಪತ್ತು ಬೈ ಎಂಬತ್ತು")
    assert detect_gestational_age(text) == 28


@pytest.mark.parametrize("text", ["28 weeks", "ಇಪ್ಪತ್ತೆಂಟು ವಾರ", "28 हफ्ते"])
def test_weeks_answer_is_understood(text):
    assert detect_gestational_age(canonicalise(text)) == 28


# --- assent only answers yes/no questions -----------------------------------

@pytest.mark.parametrize("qid", ["q01", "q02", "q03", "q04", "q06", "q07"])
def test_hum_does_not_close_a_value_question(qid):
    """Seen: "ಹೂಂ" closed q03 (last period date) with nothing recorded."""
    state = SakhiCallState()
    state.intake.pending_id = qid
    assert state.intake.record_answer("ಹೂಂ") is None
    assert not agent._answers_pending_question(state, "ಹೂಂ", "…?")


def test_yes_still_answers_a_yes_no_question():
    q = next(q for q in INTAKE_QUESTIONS if accepts_assent(q))
    state = SakhiCallState()
    state.intake.pending_id = q.id
    assert agent._answers_pending_question(state, "ಹೌದು", None)
    assert state.intake.record_answer("ಹೌದು") == q.id


def test_last_period_question_skipped_once_months_are_known():
    state = SakhiCallState()
    state.identify(gestational_weeks=24)
    state.intake.sync_applicability(state)
    assert state.intake.answered.get("q03") == "(not applicable)"


# --- language of the asked question -----------------------------------------

def test_question_text_accepts_the_language_enum():
    """str(Language.HINDI) is "Language.HINDI": Hindi calls got Kannada."""
    from sakhi_intake import question_text
    q = QUESTIONS_BY_ID["q07"]
    assert question_text(q, Language.HINDI) == q.text_hi
    assert question_text(q, Language.KANNADA) == q.text


def test_question_lock_never_speaks_kannada_on_an_english_call():
    from sakhi_intake import has_localised_text
    q = QUESTIONS_BY_ID["q07"]
    assert has_localised_text(q, Language.KANNADA)
    assert has_localised_text(q, Language.HINDI)
    assert not has_localised_text(q, Language.ENGLISH)
    state = SakhiCallState(language=Language.ENGLISH)
    state.intake.pending_id = "q07"
    assert agent._then_current_question("I am Sakhi.", state) == "I am Sakhi."


def test_tool_call_written_as_text_is_not_spoken():
    from sakhi_question_lock import QuestionLock
    lock = QuestionLock(QUESTIONS_BY_ID["q04"].text, "FAST", ())
    out = "".join(lock.feed('ಈಗಲೇ ಆಸ್ಪತ್ರೆಗೆ ಹೋಗಿ. {"name": "connect_smfmi_specialist", "parameters": {}}'))
    out += "".join(lock.finish(final=True))
    assert "connect_smfmi" not in out and "ಆಸ್ಪತ್ರೆಗೆ" in out


# --- the model cannot end the call mid-screening ----------------------------

def test_end_call_refused_while_questions_remain():
    """Seen: end_call + "Goodbye." with 15 questions to go."""
    state = SakhiCallState(language=Language.KANNADA)
    refusal = agent.end_call_refusal(state)
    assert refusal and "Do not end the call" in refusal


def test_end_call_allowed_at_the_close_or_after_handoff():
    done = SakhiCallState()
    for q in INTAKE_QUESTIONS:
        if q.id != "q17":
            done.intake.answered[q.id] = "x"
    assert agent.end_call_refusal(done) is None
    handed_over = SakhiCallState()
    handed_over.escalated = True
    assert agent.end_call_refusal(handed_over) is None


# --- repeat requests -------------------------------------------------------

@pytest.mark.parametrize("text", ["ಮತ್ತೆ ಹೇಳಿ", "ಕೇಳಿಸಲಿಲ್ಲ", "ಇನ್ನೊಮ್ಮೆ ಹೇಳಿ", "say that again"])
def test_repeat_request_is_recognised(text):
    assert agent.is_repeat_request(text)


# --- tool scoping ------------------------------------------------------------

class _Toolset:
    def __getattr__(self, name):
        return name


@pytest.mark.parametrize("stage", range(1, 8))
def test_real_toolset_has_no_duplicate_tool_names(stage):
    """Bound methods are new objects per lookup - dedupe must be by name,
    or the session dies with "duplicate function name"."""
    from sakhi_tools import ClinicalToolset
    toolset = object.__new__(ClinicalToolset)   # real bound methods; no init needed
    names = [agent._tool_name(t) for t in agent.lean_stage_tools(toolset, stage)]
    assert len(names) == len(set(names)), names


@pytest.mark.parametrize("stage", range(1, 8))
def test_every_recording_tool_is_offered_in_every_stage(stage):
    """A tool the model calls but the request lacks fails the WHOLE request on
    Groq - every provider, 4-6s of dead air ("all LLMs are unavailable")."""
    tools = agent.lean_stage_tools(_Toolset(), stage)
    assert set(tools) >= {"record_patient_intake", "record_gestational_age",
                          "record_bp", "record_symptoms", "record_anc_checkpoint"}


def test_stage_tool_comes_first_and_vitals_get_bp():
    assert agent.lean_stage_tools(_Toolset(), 3)[0] == "record_bp"
    assert agent.lean_stage_tools(_Toolset(), 4)[0] == "record_symptoms"
    # Constant set for the whole call (changing it mid-call made livekit run
    # tool calls against a stale list -> "unknown AI function" -> silence).
    assert {agent._tool_name(t) if not isinstance(t, str) else t for t in agent.lean_stage_tools(_Toolset(), 1)} \
        == {agent._tool_name(t) if not isinstance(t, str) else t for t in agent.lean_stage_tools(_Toolset(), 4)}
    assert "connect_smfmi_specialist" in agent.lean_stage_tools(_Toolset(), 2)


def test_guardrail_script_is_followed_by_the_armed_question_not_its_own():
    state = SakhiCallState(language=Language.KANNADA)
    state.intake.pending_id = "q07"
    spoken = agent._then_current_question("ನಾನು ಸಖಿ, ತಾಯಿಯ ಆರೋಗ್ಯಕ್ಕಾಗಿ ಇದ್ದೇನೆ.", state)
    assert spoken.endswith(QUESTIONS_BY_ID["q07"].text)
    assert "ರಕ್ತದೊತ್ತಡ" not in spoken
