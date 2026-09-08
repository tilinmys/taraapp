"""The "Antenatal Quick Check" rewrite (2026-09-10) - dedicated coverage for
the genuinely NEW mechanisms this introduced, not just updated expectations
for old ones (those live inline in test_sakhi_intake.py, test_anc_protocol.py,
test_sakhi_clinical.py, test_tiered_danger_signs.py, etc., each edited in
place with a comment explaining the change).

Pure mock / pure unit test - no live API calls, matching this session's
established discipline (see test_pronunciation_hygiene.py).
"""

import asyncio
import tempfile
import types
from pathlib import Path

import pytest

from sakhi_domain import BloodPressure, RiskLevel, SakhiCallState, SymptomSet
from sakhi_intake import (
    INTAKE_QUESTIONS,
    QUESTIONS_BY_ID,
    IntakeProgress,
    question_text,
    render_inventory,
)
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
# Bilingual question text
# --------------------------------------------------------------------------

def test_every_question_has_hindi_text_or_a_documented_gap():
    """Not every question is required to have text_hi (question_text() falls
    back safely), but the rewrite added text_hi to all 24 - this guards
    against a future addition silently forgetting the Hindi column, which
    would be exactly the kind of drift this file's own docstring warns
    against for the inventory itself."""
    missing = [q.id for q in INTAKE_QUESTIONS if not q.text_hi]
    assert missing == [], f"questions missing Hindi text: {missing}"


def test_hindi_inventory_differs_from_kannada_inventory():
    kn = render_inventory("kannada")
    hi = render_inventory("hindi")
    assert kn != hi, "Hindi inventory must not just be a copy of the Kannada one"
    # Every question's Hindi text must actually appear when Hindi is requested.
    for q in INTAKE_QUESTIONS:
        assert question_text(q, "hindi") in hi


def test_hindi_prompt_uses_real_hindi_question_text():
    from sakhi_prompts import SAKHI_SYSTEM_PROMPT_HI

    q01 = QUESTIONS_BY_ID["q01"]
    assert q01.text_hi in SAKHI_SYSTEM_PROMPT_HI
    # The Kannada wording for the same question must NOT be what a Hindi
    # caller's prompt shows for this question - the exact gap this rewrite
    # closed (previously the Hindi prompt silently rendered Kannada text).
    assert q01.text not in SAKHI_SYSTEM_PROMPT_HI


def test_question_text_falls_back_to_kannada_for_an_unwritten_language():
    q = QUESTIONS_BY_ID["q01"]
    assert question_text(q, "english") == q.text
    assert question_text(q, "some-unrecognized-language") == q.text


# --------------------------------------------------------------------------
# Conditional applicability (the "ER-diagram edge") - fetal heart rate
# --------------------------------------------------------------------------

def test_fetal_heart_rate_question_skipped_before_20_weeks():
    state = SakhiCallState(gestational_weeks=14)
    state.intake.sync_from_state(state)
    state.intake.sync_applicability(state)
    assert "q25" in state.intake.answered
    assert state.intake.answered["q25"] == "(not applicable)"


def test_fetal_heart_rate_question_applies_from_20_weeks():
    state = SakhiCallState(gestational_weeks=22)
    state.intake.sync_from_state(state)
    state.intake.sync_applicability(state)
    assert "q25" not in state.intake.answered


def test_fetal_heart_rate_question_applies_when_gestation_unknown():
    """Under-triage-averse default: unknown gestation means "ask it", the
    same convention sakhi_triage.py already uses for reduced fetal movement
    and preterm contractions."""
    state = SakhiCallState()
    state.intake.sync_from_state(state)
    state.intake.sync_applicability(state)
    assert "q25" not in state.intake.answered


def test_applicability_is_re_evaluated_once_gestation_becomes_known():
    """A gating fact learned mid-call must be picked up on the very next
    prompt_summary(), not just at call start."""
    state = SakhiCallState(gestational_weeks=10)
    state.prompt_summary()
    assert state.intake.answered.get("q25") == "(not applicable)"

    # Gestation corrected/learned later in the same call (e.g. a mid-call
    # gestational_weeks correction) - q25 must not stay permanently skipped
    # once it may now genuinely apply.
    state.gestational_weeks = 24
    state.intake.answered.pop("q25", None)  # simulate a fresh re-evaluation
    state.prompt_summary()
    assert "q25" not in state.intake.answered


# --------------------------------------------------------------------------
# Stall-escape (fixes "asks the same question repeatedly")
# --------------------------------------------------------------------------

def test_two_unclear_replies_do_not_close_the_question():
    p = IntakeProgress()
    p.arm_next()  # q01
    assert p.record_answer("hello") is None
    assert p.record_answer("what") is None
    assert p.pending_id == "q01", "two unclear replies must not force-close the question yet"


def test_third_unclear_reply_closes_best_effort():
    p = IntakeProgress()
    p.arm_next()  # q01
    assert p.record_answer("hello") is None
    assert p.record_answer("what") is None
    closed = p.record_answer("huh")
    assert closed == "q01", "the third consecutive unclear reply must close the question"
    assert p.answered["q01"].endswith("(unclear, best-effort)")
    assert p.pending_id is None


def test_stall_count_resets_on_a_normal_close():
    p = IntakeProgress()
    p.arm_next()  # q01
    p.record_answer("hello")  # one stall
    assert p.record_answer("Suma, twenty four") == "q01"  # real answer closes normally
    # The next question starts with a clean stall count - two more unclear
    # replies must NOT immediately trip the (already-exhausted) ceiling.
    p.arm_next()  # q02
    assert p.record_answer("hello") is None
    assert p.record_answer("what") is None
    assert p.pending_id == "q02", "a resolved question's stall count must not carry over"


def test_stall_count_resets_when_a_different_question_is_armed():
    p = IntakeProgress()
    p.arm_next()  # q01
    p.record_answer("hello")  # one stall on q01
    p.record_answer("Suma, twenty four")  # closes q01 normally
    nxt = p.arm_next()
    assert nxt.id == "q02"
    assert p.record_answer("hello") is None
    assert p.record_answer("what") is None
    assert p.pending_id == "q02", "arming a new question must not inherit q01's stall count"


# --------------------------------------------------------------------------
# tools_for() no longer silently drops record_anc_checkpoint et al.
# --------------------------------------------------------------------------

def test_record_anc_checkpoint_survives_the_per_turn_rescope(toolset):
    """The real, previously-undiscovered bug this rewrite fixed: tools_for()
    - the list used for agent.py's PER-TURN re-scope - never included
    record_anc_checkpoint, record_patient_intake, or
    check_pregnancy_risk_factors, even though they were reachable at initial
    Agent construction (as_list()). From the second turn onward the model
    had no live tool call left to reach them at all."""
    names = [f.__name__ for f in toolset.tools_for(SakhiCallState())]
    assert "record_anc_checkpoint" in names
    assert "record_patient_intake" in names
    assert "check_pregnancy_risk_factors" in names


def test_record_anc_checkpoint_available_in_every_phase(toolset):
    for state in (
        SakhiCallState(),
        SakhiCallState(patient_id="M-201", name="Suma Gowda"),
    ):
        names = [f.__name__ for f in toolset.tools_for(state)]
        assert "record_anc_checkpoint" in names


# --------------------------------------------------------------------------
# New symptom fields wired end to end: record_symptoms -> triage
# --------------------------------------------------------------------------

def test_record_symptoms_bundle_records_every_new_field_in_one_call(toolset):
    """One grouped ASK question ("any fever, urinary symptoms,
    breathlessness, palpitations, or severe vomiting?") must be answerable
    with a single record_symptoms call naming every true finding - the
    guard against the model splitting it into separate follow-up questions."""
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.record_symptoms(
            _ctx(state), urinary_symptoms=True, palpitations=True, severe_vomiting=True,
        )
        assert state.symptoms.urinary_symptoms
        assert state.symptoms.palpitations
        assert state.symptoms.severe_vomiting
        assert not state.symptoms.fever
        assert not state.symptoms.breathlessness
    _run(go())


def test_fever_alone_is_high_risk(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.record_symptoms(_ctx(state), fever=True)
        assert state.risk_level is RiskLevel.HIGH_RISK
        assert state.intake_locked
        assert state.intake_lock_reason == "fever"
    _run(go())


def test_breathlessness_alone_is_high_risk(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.record_symptoms(_ctx(state), breathlessness=True)
        assert state.risk_level is RiskLevel.HIGH_RISK
        assert state.intake_lock_reason == "breathlessness"
    _run(go())


def test_urinary_symptoms_alone_is_not_high_risk(toolset):
    """The other four new fields (upper_abdominal_pain, urinary_symptoms,
    palpitations, severe_vomiting) are moderate-risk, not Tier-1-alone."""
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.record_symptoms(_ctx(state), urinary_symptoms=True)
        assert state.risk_level is RiskLevel.OBSERVATION
        assert not state.intake_locked
    _run(go())


def test_preterm_contractions_are_high_risk():
    result = TRIAGE.assess(None, SymptomSet(contractions=True), gestational_weeks=30)
    assert result.level is RiskLevel.HIGH_RISK


def test_term_contractions_are_not_escalated():
    result = TRIAGE.assess(None, SymptomSet(contractions=True), gestational_weeks=39)
    assert result.level is RiskLevel.OBSERVATION


def test_contractions_with_unknown_gestation_are_treated_as_preterm():
    result = TRIAGE.assess(None, SymptomSet(contractions=True))
    assert result.level is RiskLevel.HIGH_RISK


# --------------------------------------------------------------------------
# Deterministic detection of the new symptom vocabulary
# --------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,field", [
    ("ಜ್ವರ ಬಂದಿದೆ", "fever"),
    ("बुखार है", "fever"),
    ("ಉಸಿರಾಟದ ತೊಂದರೆ ಆಗ್ತಿದೆ", "breathlessness"),
    ("सांस लेने में तकलीफ है", "breathlessness"),
    ("ಸಂಕೋಚನ ಶುರುವಾಗಿದೆ", "contractions"),
    ("पेट के ऊपरी हिस्से में दर्द है", "upper_abdominal_pain"),
    ("ಮೂತ್ರದ ತೊಂದರೆ ಇದೆ", "urinary_symptoms"),
    ("ಎದೆ ಢವಢವ ಅನ್ನಿಸ್ತಿದೆ", "palpitations"),
    ("ವಿಪರೀತ ವಾಂತಿ ಆಗ್ತಿದೆ", "severe_vomiting"),
])
def test_new_symptom_phrases_are_detected(phrase, field):
    from sakhi_domain import detect_symptoms

    assert getattr(detect_symptoms(phrase), field), f"{phrase!r} should have set {field}"


def test_new_symptom_phrases_are_negation_aware():
    from sakhi_domain import detect_symptoms

    assert not detect_symptoms("ಜ್ವರ ಇಲ್ಲ").fever
    assert not detect_symptoms("no breathlessness").breathlessness
