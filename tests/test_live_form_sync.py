"""Module C (2026-08-30): Live Form Sync + one-tap keypad correction +
MCP/RCH-shaped end-of-visit card.

Covers:
  - ClinicalToolset.apply_field_correction() - the whitelist rejection, the
    round-trip mutation + re-triage, and the Tier-1 lock firing on a
    corrected value, exactly the way agent.py's `submit_correction` RPC
    handler calls it (this module has no LiveKit dependency, so the RPC
    transport layer itself is not exercised here - see agent.py).
  - status_server.mcp_card_for_report()'s field mapping, snapshotted against
    a fully-populated call.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from sakhi_domain import BloodPressure, RiskLevel, SakhiCallState, SymptomSet
from sakhi_sql_repository import SQLitePatientRepository
from sakhi_tools import CORRECTABLE_FIELDS, ClinicalToolset
from sakhi_triage import TRIAGE


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def toolset():
    repo = _run(SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load())
    return ClinicalToolset(repo, TRIAGE)


def test_a_correction_also_emits_live_form_sync(toolset):
    """A keypad correction (e.g. from a supervisor's tablet reviewing the
    same call) must reach every viewer polling the log stream, not just the
    device that made it - same event a plain record_bp/record_symptoms
    already emits."""
    events = []
    toolset._event = lambda *a, **kw: events.append((a, kw))

    async def go():
        state = SakhiCallState(patient_id="M-201")
        state.record_blood_pressure(BloodPressure(120, 80))
        await toolset.apply_field_correction(state, "systolic", 130)
    _run(go())

    sync_events = [kw for _, kw in events if kw.get("event") == "live_form_sync"]
    assert sync_events, "apply_field_correction must emit a live_form_sync event"


# --- whitelist rejection -------------------------------------------------

def test_apply_field_correction_rejects_a_non_whitelisted_field(toolset):
    """Same defensive posture as sakhi_sql_repository.py's FLAGGABLE_FIELDS
    rejection test - a raw field name from an RPC payload must never reach
    a mutator dispatch unchecked."""
    async def go():
        state = SakhiCallState(patient_id="M-201")
        with pytest.raises(ValueError, match="not a correctable field"):
            await toolset.apply_field_correction(state, "patient_id", "M-999")
    _run(go())


def test_correctable_fields_matches_what_the_rpc_handler_advertises():
    # Guards against the whitelist silently drifting from what the frontend
    # keypad is told it can send.
    assert CORRECTABLE_FIELDS == frozenset({
        "systolic", "diastolic", "gestational_weeks",
        "headache", "blurry_vision", "swelling",
        "vaginal_bleeding", "severe_abdominal_pain",
        # ANC red flags added 2026-09-02 - correctable for the same reason
        # the others are: a wrongly-detected fit or "baby not moving" locks
        # the intake and pages a specialist, so the ASHA worker needs a way
        # to take it back without another voice round-trip.
        "convulsions", "reduced_fetal_movement",
        # Antenatal Quick Check rewrite (2026-09-10) - new SymptomSet
        # fields, correctable for the same reason as the ones above.
        "contractions", "upper_abdominal_pain", "fever", "urinary_symptoms",
        "breathlessness", "palpitations", "severe_vomiting",
    })


# --- round trip: correction updates state and re-triages -----------------

def test_correcting_systolic_updates_state_and_retriages(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        state.record_blood_pressure(BloodPressure(120, 80))
        state.record_assessment(TRIAGE.assess(state.blood_pressure, state.symptoms))

        await toolset.apply_field_correction(state, "systolic", 170)

        assert state.blood_pressure.systolic == 170
        assert state.blood_pressure.diastolic == 80  # untouched half preserved
        assert state.assessment.level is RiskLevel.HIGH_RISK
    _run(go())


def test_correcting_a_symptom_boolean_accumulates_like_record_symptoms(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        state.record_symptoms(SymptomSet(headache=True))

        await toolset.apply_field_correction(state, "swelling", True)

        assert state.symptoms.headache, "an earlier symptom must not be erased by a correction"
        assert state.symptoms.swelling
    _run(go())


def test_correcting_gestational_weeks(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.apply_field_correction(state, "gestational_weeks", 32)
        assert state.gestational_weeks == 32
    _run(go())


def test_correcting_one_bp_number_before_any_bp_recorded_is_rejected(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        with pytest.raises(ValueError, match="both have been recorded once"):
            await toolset.apply_field_correction(state, "systolic", 130)
    _run(go())


def test_correcting_a_danger_sign_symptom_locks_intake(toolset):
    """A corrected value can itself be the thing that reaches HIGH_RISK - the
    lock must fire from this path exactly as it does from record_symptoms."""
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.apply_field_correction(state, "vaginal_bleeding", True)
        assert state.assessment.level is RiskLevel.HIGH_RISK
        assert state.intake_locked
        assert state.intake_lock_reason == "vaginal_bleeding"
    _run(go())


# --- MCP/RCH card mapping -------------------------------------------------

@pytest.fixture
def sql_path(tmp_path):
    return tmp_path / "sakhi.sqlite3"


def _seed_full_call(sql_path):
    async def go():
        repo = await SQLitePatientRepository(sql_path).load()
        mother = await repo.create_patient(
            name="Suma Gowda", age=24, village="Kamalnagar",
            phone="9876543210", gestational_weeks=28,
        )
        bp = BloodPressure(170, 115)
        assessment = TRIAGE.assess(bp, SymptomSet(vaginal_bleeding=True))
        await repo.record_blood_pressure(mother.patient_id, bp, assessment)
        await repo.record_symptoms(mother.patient_id, SymptomSet(vaginal_bleeding=True))
        await repo.log_teleconsult(
            mother.patient_id, "Tier-1 danger sign", assessment,
            trigger_sign="vaginal_bleeding",
        )
        report_id = await repo.save_call_report({
            "session_id": "room-abc123",
            "patient_id": mother.patient_id,
            "language": "kn",
            "risk_level": assessment.level.value,
            "intake_answered": 10,
            "intake_total": 26,
            "escalated": True,
            "duration_ms": 45210.0,
        })
        await repo.close()
        return report_id, mother.patient_id

    return _run(go())


def test_mcp_card_maps_the_expected_fields_by_report_id(sql_path, monkeypatch):
    monkeypatch.setenv("SAKHI_DB_PATH", str(sql_path))
    import status_server

    report_id, _ = _seed_full_call(sql_path)
    card = status_server.mcp_card_for_report(report_id=report_id)

    assert card is not None
    assert card["mother_name"] == "Suma Gowda"
    assert card["age"] == 24
    assert card["village"] == "Kamalnagar"
    assert card["gestational_weeks"] == 28
    assert card["blood_pressure"] == "170/115"
    assert "Vaginal bleeding" in card["danger_signs"]
    assert card["risk_category"] == "HIGH_RISK"
    assert card["referral_status"] == "Referred to specialist"
    assert card["referral_trigger"] == "vaginal_bleeding"
    assert card["questions_answered"] == "10/26"
    assert card["draft_needs_form_verification"] is True


def test_mcp_card_looks_up_by_session_id_too(sql_path, monkeypatch):
    """The frontend page knows the LiveKit room name (session_id) right
    after end_call, not the internal SQL row id - this is the lookup path
    it actually uses."""
    monkeypatch.setenv("SAKHI_DB_PATH", str(sql_path))
    import status_server

    _seed_full_call(sql_path)
    card = status_server.mcp_card_for_report(session_id="room-abc123")
    assert card is not None
    assert card["mother_name"] == "Suma Gowda"


def test_mcp_card_returns_none_for_an_unknown_report(sql_path, monkeypatch):
    monkeypatch.setenv("SAKHI_DB_PATH", str(sql_path))
    import status_server

    _seed_full_call(sql_path)
    assert status_server.mcp_card_for_report(report_id=999999) is None
    assert status_server.mcp_card_for_report(session_id="no-such-room") is None


def test_mcp_card_returns_none_with_no_lookup_key(sql_path, monkeypatch):
    monkeypatch.setenv("SAKHI_DB_PATH", str(sql_path))
    import status_server

    assert status_server.mcp_card_for_report() is None
