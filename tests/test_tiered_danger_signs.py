"""Tier-1 danger signs (2026-08-30): vaginal bleeding and severe abdominal
pain/leaking fluid reach HIGH_RISK on their own, the same way severe BP
already did - and once HIGH_RISK is reached by any path, intake locks and
the questionnaire stops for the rest of the call.

CALIBRATION CHANGE (2026-09-10): this file's BP-alone calibration used to be
guarded as deliberately UNCHANGED - a single hypertensive reading
(140-159/90-109) with no symptoms landed at MODERATE, not an immediate lock,
because an earlier brief's literal "BP>=140/90 is its own Tier-1 sign"
reading was considered and explicitly NOT implemented as stated.

That decision was revisited when the intake was rewired onto a real
"Antenatal Quick Check" PDF the user supplied, whose own RED FLAGS list
states BP>=140/90 as an automatic urgent-referral trigger. Put to the user
directly a second time, the answer this time was "match the PDF literally" -
see sakhi_triage.py's module docstring for the full reasoning. Plain
hypertensive-alone now ALSO reaches HIGH_RISK and locks intake, same as the
signs this file was originally written to test. The two tests that used to
guard the old calibration (`test_bp_alone_calibration_is_unchanged`,
`test_moderate_bp_alone_does_not_lock`) are updated below to guard the new
one instead, not silently deleted.
"""

import asyncio
import json
import tempfile
import types
from pathlib import Path

import pytest

from sakhi_domain import (
    BloodPressure,
    RiskLevel,
    SakhiCallState,
    SymptomSet,
    danger_sign_lock_reason,
    detect_symptoms,
)
from sakhi_sql_repository import SQLitePatientRepository
from sakhi_tools import ClinicalToolset
from sakhi_triage import TRIAGE, TriageEngine


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def toolset():
    repo = _run(SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load())
    return ClinicalToolset(repo, TRIAGE)


def _ctx(state):
    return types.SimpleNamespace(userdata=state)


# --- SymptomSet: new fields --------------------------------------------

def test_new_symptom_fields_default_false_and_accumulate():
    s = SymptomSet(vaginal_bleeding=True)
    assert s.any_present
    assert "vaginal bleeding" in s.present_names

    merged = s.merged_with(SymptomSet(severe_abdominal_pain=True))
    assert merged.vaginal_bleeding and merged.severe_abdominal_pain
    assert "vaginal bleeding" in merged.present_names
    assert "severe abdominal pain or leaking fluid" in merged.present_names


# --- TriageEngine: Tier-1-alone triggers --------------------------------

def test_vaginal_bleeding_alone_is_high_risk_regardless_of_bp():
    result = TRIAGE.assess(None, SymptomSet(vaginal_bleeding=True))
    assert result.level is RiskLevel.HIGH_RISK
    assert result.requires_escalation


def test_severe_abdominal_pain_alone_is_high_risk_regardless_of_bp():
    result = TRIAGE.assess(BloodPressure(118, 76), SymptomSet(severe_abdominal_pain=True))
    assert result.level is RiskLevel.HIGH_RISK


def test_bp_alone_now_matches_the_pdf_literally():
    """2026-09-10: replaces test_bp_alone_calibration_is_unchanged. A plain
    hypertensive reading (not severe, no symptoms) now reaches HIGH_RISK
    immediately, per an explicit user decision matching the "Antenatal
    Quick Check" source PDF's own RED FLAGS list - see sakhi_triage.py's
    module docstring. RiskLevel.MODERATE is no longer reachable by any path."""
    result = TRIAGE.assess(BloodPressure(145, 92), SymptomSet())
    assert result.level is RiskLevel.HIGH_RISK
    assert result.requires_escalation


def test_severe_bp_alone_is_still_high_risk_as_before():
    """Unrelated to the new signs - confirms the pre-existing severe-BP-alone
    rule was not disturbed by adding the danger_sign_alone branch above it."""
    result = TRIAGE.assess(BloodPressure(165, 115), SymptomSet())
    assert result.level is RiskLevel.HIGH_RISK


# --- detect_symptoms: new idiom mappings --------------------------------

@pytest.mark.parametrize("phrase,field", [
    ("ಹೊಟ್ಟೆ ಕಲ್ಲು ತರ ಗಟ್ಟಿಯಾಗಿದೆ", "severe_abdominal_pain"),
    ("ರಕ್ತಸ್ರಾವ ಆಗ್ತಿದೆ", "vaginal_bleeding"),
    ("ಬ್ಲೀಡಿಂಗ್ ಆಗ್ತಿದೆ", "vaginal_bleeding"),
    ("ಕಣ್ಣು ಕತ್ತಲೆ ಬರ್ತಿದೆ", "blurry_vision"),
    ("ತಲೆಸುತ್ತು ಬರ್ತಿದೆ", "blurry_vision"),
])
def test_new_idiom_phrases_map_to_the_correct_field(phrase, field):
    result = detect_symptoms(phrase)
    assert getattr(result, field), f"{phrase!r} should have set {field}"


def test_new_idioms_are_negation_aware():
    result = detect_symptoms("ರಕ್ತಸ್ರಾವ ಇಲ್ಲ")
    assert not result.vaginal_bleeding, "explicit denial must not register the symptom"


def test_pedal_edema_idiom_already_worked_before_this_change():
    """The brief's pedal-edema mapping was already fully covered by the
    existing `swelling` field - this is a non-regression check, not new
    behavior."""
    result = detect_symptoms("ಕಾಲು ಊದಿಕೊಂಡಿದೆ")
    assert result.swelling


# --- danger_sign_lock_reason precedence ---------------------------------

def test_lock_reason_precedence_matches_classify_order():
    state = SakhiCallState(patient_id="M-1")
    state.symptoms = SymptomSet(vaginal_bleeding=True, severe_abdominal_pain=True)
    state.blood_pressure = BloodPressure(165, 115)
    assert danger_sign_lock_reason(state) == "vaginal_bleeding"

    state2 = SakhiCallState(patient_id="M-2")
    state2.symptoms = SymptomSet(severe_abdominal_pain=True)
    state2.blood_pressure = BloodPressure(165, 115)
    assert danger_sign_lock_reason(state2) == "severe_abdominal_pain"

    state3 = SakhiCallState(patient_id="M-3")
    state3.blood_pressure = BloodPressure(165, 115)
    assert danger_sign_lock_reason(state3) == "severe_bp"

    state4 = SakhiCallState(patient_id="M-4")
    state4.blood_pressure = BloodPressure(145, 92)
    assert danger_sign_lock_reason(state4) == "symptomatic_bp"


# --- lock_intake_on_danger_sign() is idempotent -------------------------

def test_lock_is_idempotent_and_keeps_the_first_reason():
    state = SakhiCallState()
    state.lock_intake_on_danger_sign("vaginal_bleeding")
    state.lock_intake_on_danger_sign("severe_abdominal_pain")
    assert state.intake_locked
    assert state.intake_lock_reason == "vaginal_bleeding"


# --- prompt_summary(): locked state replaces the NEXT QUESTION line -----

def test_prompt_summary_replaces_next_question_when_locked():
    state = SakhiCallState(patient_id="M-1", name="Test Mother", name_confirmed=True)
    state.intake.pending_id = "q07"
    state.lock_intake_on_danger_sign("vaginal_bleeding")

    summary = state.prompt_summary()
    assert "INTAKE LOCKED" in summary
    assert "NEXT QUESTION" not in summary


def test_prompt_summary_is_normal_when_not_locked():
    state = SakhiCallState(patient_id="M-1", name="Test Mother", name_confirmed=True)
    summary = state.prompt_summary()
    assert "INTAKE LOCKED" not in summary


# --- _retriage() (the tool-call path: record_bp/record_symptoms) locks too --
#
# This is the path most likely to actually reach HIGH_RISK in practice (a
# severe BP reading called in directly), and it is a SEPARATE code path from
# agent.py's deterministic re-triage on raw speech - both needed the lock
# wired in independently, so both are tested independently here.

def test_record_bp_alone_reaching_severe_locks_intake(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.record_bp(_ctx(state), 165, 115)
        assert state.intake_locked
        assert state.intake_lock_reason == "severe_bp"
    _run(go())


def test_record_symptoms_alone_reaching_bleeding_locks_intake(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        # record_symptoms only has the original three params - vaginal
        # bleeding/severe abdominal pain are set via the deterministic
        # detector (agent.py's _capture_symptoms), not this tool's own
        # signature (see Module B's plan: no new record_clinical_batch).
        # Exercise the lock here by setting the symptom directly, as
        # _capture_symptoms would after detect_symptoms() matches it.
        state.record_symptoms(SymptomSet(vaginal_bleeding=True))
        await toolset.record_bp(_ctx(state), 118, 76)  # any tool call re-triages
        assert state.intake_locked
        assert state.intake_lock_reason == "vaginal_bleeding"
    _run(go())


def test_hypertensive_bp_alone_now_locks(toolset):
    """2026-09-10: replaces test_moderate_bp_alone_does_not_lock. A plain
    hypertensive reading now reaches HIGH_RISK (see
    test_bp_alone_now_matches_the_pdf_literally above) and therefore locks
    intake too, via the same _retriage()-lock path every other HIGH_RISK
    trigger uses."""
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.record_bp(_ctx(state), 145, 92)
        assert state.assessment.level is RiskLevel.HIGH_RISK
        assert state.intake_locked
        assert state.intake_lock_reason == "symptomatic_bp"
    _run(go())


def test_high_risk_instruction_includes_the_matched_advisory(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        result = json.loads(await toolset.record_bp(_ctx(state), 165, 115))
        assert result["risk_level"] == "HIGH_RISK"
        # The advisory text itself is Kannada and not asserted verbatim here
        # (draft content, flagged for clinical review) - just confirm SOME
        # advisory landed in the instruction, not an empty placeholder.
        assert "ಆಸ್ಪತ್ರೆ" in result["instruction"] or "advisory" in result["instruction"].lower()
    _run(go())


def test_connect_smfmi_specialist_logs_the_trigger_sign(toolset):
    async def go():
        # teleconsults.patient_id has a real FK to mothers(patient_id) with
        # foreign_keys=ON - a hardcoded id with no matching row would fail
        # both the BP write and the teleconsult log, so the patient is
        # actually registered first, same as a real call would.
        record = await toolset._repo.create_patient(name="Test Mother")
        state = SakhiCallState(patient_id=record.patient_id)
        await toolset.record_bp(_ctx(state), 165, 115)
        await toolset.connect_smfmi_specialist(_ctx(state), "severe BP")
        # _query is sync (blocking sqlite I/O), not a coroutine - called
        # directly here, same as production code only ever calls it from
        # inside an asyncio.to_thread-wrapped function.
        rows = toolset._repo._query(  # noqa: SLF001 - test-only introspection
            "SELECT trigger_sign FROM teleconsults WHERE patient_id = ?", (record.patient_id,)
        )
        assert rows[0]["trigger_sign"] == "severe_bp"
    _run(go())
