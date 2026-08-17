"""Regressions from the live red-team call of 2026-08-15.

Every test here corresponds to a defect that actually occurred on a real call,
not a hypothetical. The call log is quoted in each docstring so the failure is
recognisable if it ever returns.

Run:  python -m pytest tests/test_sakhi_regressions.py -q
"""

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sakhi_domain import (  # noqa: E402
    BloodPressure,
    RiskLevel,
    SakhiCallState,
    SymptomSet,
    detect_symptoms,
)
from sakhi_repository import (  # noqa: E402
    MockSixtyDBRepository,
    match_key,
    name_similarity,
)
from sakhi_tools import ClinicalToolset  # noqa: E402
from sakhi_triage import TRIAGE  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "db.json"
    path.write_text(json.dumps({
        "patients": [
            {"patient_id": "M-201", "name": "Suma Gowda", "age": 24,
             "village": "Kamalnagar", "phone": "9876543210",
             "gestational_weeks": 28, "risk_status": "ROUTINE",
             "history": [], "symptoms": {}},
            {"patient_id": "M-202", "name": "Priya Patil", "age": 29,
             "village": "Aurad", "phone": "8765432109",
             "gestational_weeks": 33, "risk_status": "MODERATE",
             "history": [], "symptoms": {}},
        ],
        "bp_records": [], "teleconsult_logs": [],
    }), encoding="utf-8")
    return MockSixtyDBRepository(path, flush_delay=0.01)


# --------------------------------------------------------------------------
# 1. Under-triage: a stated symptom was never recorded
#    Log: "Symptoms screened - none reported" after the worker said the mother
#    had a severe headache. Triage stayed MODERATE; should have been HIGH_RISK.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "ಸುಮಾ ಅವರಿಗೆ ತೀವ್ರವಾದ ತಲೆನೋವು ಇದೆ",              # Kannada
    "ಇದು ತೀವ್ರವಾದ ತಲೆನೋವಿನೊಂದಿಗೆ ಸೇರಿಕೊಂಡರೆ",        # inflected, from the real log
    "ಲೈಕ್ ಎ ಹೆಡ್ ಏಕ್",                                # English transliterated by STT
    "she has a severe headache",
    "सिरदर्द है",                                       # Hindi
])
def test_headache_is_detected_however_it_is_spoken(utterance):
    assert detect_symptoms(utterance).headache


@pytest.mark.parametrize("utterance", [
    "ತಲೆನೋವು ಇಲ್ಲ",
    "no headache",
    "no swelling and no headache",
    "सिरदर्द नहीं",
])
def test_denied_symptoms_are_not_recorded(utterance):
    """Otherwise every screening question would flag itself the moment it was
    answered 'no'."""
    assert not detect_symptoms(utterance).any_present


def test_symptom_heard_in_speech_flips_moderate_to_high_risk():
    """The exact clinical miss from the live call."""
    state = SakhiCallState()
    state.record_blood_pressure(BloodPressure(145, 95))
    state.record_assessment(TRIAGE.assess(state.blood_pressure, state.symptoms))
    assert state.risk_level is RiskLevel.MODERATE

    state.record_symptoms(detect_symptoms("ತೀವ್ರವಾದ ತಲೆನೋವು ಇದೆ"))
    state.record_assessment(TRIAGE.assess(state.blood_pressure, state.symptoms))
    assert state.risk_level is RiskLevel.HIGH_RISK


def test_model_reporting_no_symptoms_cannot_erase_a_heard_one():
    """SymptomSet only ORs, so record_symptoms(false,false,false) - which is
    what the model actually sent - must not clear a detected symptom."""
    state = SakhiCallState()
    state.record_symptoms(detect_symptoms("ತೀವ್ರವಾದ ತಲೆನೋವು ಇದೆ"))
    state.record_symptoms(SymptomSet(headache=False, blurry_vision=False, swelling=False))
    assert state.symptoms.headache


# --------------------------------------------------------------------------
# 2. Over-escalation: specialist connected on a MODERATE
#    Log: "Triage: MODERATE ... No danger symptoms" then
#         "SMFMI escalation - Specialist connected for M-203"
# --------------------------------------------------------------------------

def test_escalation_is_refused_when_triage_does_not_require_it(repo):
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState(patient_id="M-201")
        ctx = types.SimpleNamespace(userdata=state)

        await tools.record_bp(ctx, 145, 95)
        assert state.risk_level is RiskLevel.MODERATE

        result = json.loads(await tools.connect_smfmi_specialist(ctx, "moderate risk"))
        assert result["connected"] is False
        assert not state.escalated
        assert repo._data["teleconsult_logs"] == [], "bogus escalation was audited"
    _run(go())


def test_escalation_is_allowed_at_high_risk(repo):
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState(patient_id="M-201")
        ctx = types.SimpleNamespace(userdata=state)

        await tools.record_bp(ctx, 145, 95)
        await tools.record_symptoms(ctx, headache=True)
        assert state.risk_level is RiskLevel.HIGH_RISK

        result = json.loads(await tools.connect_smfmi_specialist(ctx, "BP + headache"))
        assert result["connected"] is True
        assert len(repo._data["teleconsult_logs"]) == 1
    _run(go())


def test_escalation_allowed_before_any_assessment(repo):
    """No decision exists to contradict; refusing would block a genuine
    emergency raised before a reading was taken."""
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        ctx = types.SimpleNamespace(userdata=SakhiCallState(patient_id="M-201"))
        result = json.loads(await tools.connect_smfmi_specialist(ctx, "mother collapsed"))
        assert result["connected"] is True
    _run(go())


# --------------------------------------------------------------------------
# 3. Script mismatch created a duplicate patient
#    Log: "Patient search miss - name=ಸುಮಾ ಗೌಡ" then
#         "Mother registered - Suman Goud (M-203)"
# --------------------------------------------------------------------------

def test_kannada_script_name_finds_a_latin_stored_record(repo):
    async def go():
        await repo.load()
        hits = await repo.find_patients(name="ಸುಮಾ ಗೌಡ")
        assert hits, "the exact lookup that failed on the live call"
        assert hits[0].name == "Suma Gowda"
    _run(go())


def test_devanagari_name_matches_too(repo):
    async def go():
        await repo.load()
        hits = await repo.find_patients(name="ಪ್ರಿಯಾ ಪಾಟೀಲ್")
        assert hits and hits[0].name == "Priya Patil"
    _run(go())


def test_spelling_variant_matches_instead_of_creating_a_duplicate(repo):
    """'Suman Goud' is what the model actually registered as a new mother."""
    async def go():
        await repo.load()
        hits = await repo.find_patients(name="Suman Goud")
        assert hits and hits[0].name == "Suma Gowda"
    _run(go())


def test_unrelated_names_still_do_not_match(repo):
    async def go():
        await repo.load()
        assert await repo.find_patients(name="Anitha Rao") == []
        assert await repo.find_patients(name="ರಮೇಶ್") == []
    _run(go())


def test_match_key_is_script_independent():
    assert match_key("ಪ್ರಿಯಾ ಪಾಟೀಲ್") == match_key("Priya Patil")
    assert name_similarity("ಸುಮಾ ಗೌಡ", "Suma Gowda") > 0.8
    assert name_similarity("Suma Gowda", "Priya Patil") < 0.3


# --------------------------------------------------------------------------
# 4. LLM attempt timeout was below the observed tail
#    Log: 'all LLMs failed after 1.086s' alongside a valid ttft=2.210s
# --------------------------------------------------------------------------

def test_llm_attempt_timeouts_clear_each_provider_group_tail():
    """Each group's budget must exceed that group's OWN measured worst case.

    Superseded the single LLM_ATTEMPT_TIMEOUT. One global number could not
    serve a 604ms provider and an 1842ms one at the same time: at 1.8s the
    OpenRouter tier was cancelled before its median response, so it could
    never succeed - it only ever cost time.
    """
    import os

    groq = float(os.getenv("GROQ_ATTEMPT_TIMEOUT", "1.5"))
    openrouter = float(os.getenv("OPENROUTER_ATTEMPT_TIMEOUT", "4.0"))
    outer = float(os.getenv("LLM_GROUP_TIMEOUT", "30.0"))

    assert groq >= 1.2, f"groq budget {groq}s is under its 1113ms observed max"
    assert openrouter >= 3.0, (
        f"openrouter budget {openrouter}s is under its 2713ms observed max; "
        "the tier would be cancelled before it could ever answer"
    )
    assert outer > max(groq, openrouter), (
        "the outer backstop must not cancel a group mid-attempt"
    )
