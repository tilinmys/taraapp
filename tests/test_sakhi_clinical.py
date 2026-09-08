"""Clinical core tests: domain value objects, triage matrix, repository.

Standalone (no LiveKit, no network, no API keys) so the clinical safety logic
can be verified in CI on any machine - the same constraint the existing
guardrails/fast-path suites follow.

Run:  python -m pytest tests/test_sakhi_clinical.py -q
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sakhi_domain import (  # noqa: E402
    BloodPressure,
    BloodPressureError,
    Language,
    RiskLevel,
    SakhiCallState,
    SymptomSet,
    clean_person_name,
)
from sakhi_repository import MockSixtyDBRepository, PatientRecord, normalize_phone  # noqa: E402
from sakhi_triage import TRIAGE, TriageEngine  # noqa: E402


# --------------------------------------------------------------------------
# Language
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("kn", Language.KANNADA),
        ("kn-IN", Language.KANNADA),
        ("KN_in", Language.KANNADA),
        ("hi", Language.HINDI),
        ("hi-IN", Language.HINDI),
        ("en", Language.ENGLISH),
        ("en-IN", Language.ENGLISH),
    ],
)
def test_language_parses_every_shape_used_across_the_pipeline(raw, expected):
    assert Language.parse(raw) is expected


@pytest.mark.parametrize("raw,expected", [
    ("kannada", Language.KANNADA),
    ("hindi", Language.HINDI),
    ("english", Language.ENGLISH),
    ("Hindi", Language.HINDI),
    ("  hindi  ", Language.HINDI),
])
def test_language_accepts_the_spelled_out_names_the_dashboard_sends(raw, expected):
    """The picker in frontend/src/app/page.tsx sends "kannada"/"hindi", not
    BCP-47 codes. Matching codes only meant selecting Hindi silently produced a
    Kannada agent."""
    assert Language.parse(raw) is expected


def test_language_enum_members_are_not_polluted_by_the_alias_table():
    assert [m.name for m in Language] == ["KANNADA", "HINDI", "ENGLISH"]


@pytest.mark.parametrize("raw", [None, "", "  ", "xx", "klingon", 42, object()])
def test_language_never_raises_on_junk(raw):
    """Sarvam goes silent on unknown codes, which makes the agent deaf. An
    unparseable language must fall back, never propagate an exception."""
    assert Language.parse(raw) is Language.KANNADA


def test_language_carries_provider_codes_and_honorifics():
    assert Language.KANNADA.sarvam_code == "kn-IN"
    assert Language.HINDI.sarvam_code == "hi-IN"
    assert Language.KANNADA.honorific == "ಅಕ್ಕಾರ"
    assert Language.HINDI.honorific == "दीदी"


# --------------------------------------------------------------------------
# BloodPressure
# --------------------------------------------------------------------------

def test_bp_thresholds():
    assert not BloodPressure(120, 80).is_hypertensive
    assert BloodPressure(140, 85).is_hypertensive      # systolic alone
    assert BloodPressure(130, 90).is_hypertensive      # diastolic alone
    assert not BloodPressure(145, 95).is_severe
    assert BloodPressure(160, 95).is_severe
    assert BloodPressure(150, 110).is_severe


@pytest.mark.parametrize(
    "systolic,diastolic",
    [
        (140, 900),   # STT heard "ninety" as "nine hundred"
        (1400, 90),
        (90, 140),    # transposed
        (30, 20),     # implausible
        (0, 0),
    ],
)
def test_bp_rejects_implausible_readings(systolic, diastolic):
    """A mis-transcribed reading must surface as a re-ask, never be recorded."""
    with pytest.raises(BloodPressureError):
        BloodPressure(systolic, diastolic)


def test_bp_is_immutable():
    bp = BloodPressure(120, 80)
    with pytest.raises(Exception):
        bp.systolic = 200


# --------------------------------------------------------------------------
# SymptomSet
# --------------------------------------------------------------------------

def test_symptoms_accumulate_and_never_erase():
    """A later 'no swelling' must not clear an earlier 'yes headache'."""
    merged = SymptomSet(headache=True).merged_with(SymptomSet(swelling=True))
    assert merged.headache and merged.swelling
    assert merged.merged_with(SymptomSet()).headache


def test_symptoms_reject_non_bool():
    with pytest.raises(TypeError):
        SymptomSet(headache="yes")


# --------------------------------------------------------------------------
# Triage matrix - the product's core clinical rule
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bp,symptoms,expected",
    [
        # High BP + any danger symptom -> red alert
        (BloodPressure(145, 95), SymptomSet(headache=True), RiskLevel.HIGH_RISK),
        (BloodPressure(145, 95), SymptomSet(blurry_vision=True), RiskLevel.HIGH_RISK),
        (BloodPressure(145, 95), SymptomSet(swelling=True), RiskLevel.HIGH_RISK),
        # High BP alone -> HIGH_RISK too (2026-09-10, explicit user decision:
        # matches the "Antenatal Quick Check" source PDF's own RED FLAGS
        # list literally - BP>=140/90 is its own automatic urgent-referral
        # trigger there, with no symptomatic qualifier. Was RiskLevel.MODERATE
        # (a 48h re-check) before this change - see sakhi_triage.py's module
        # docstring for the full reasoning and the alternative that was
        # offered and not chosen.
        (BloodPressure(145, 95), SymptomSet(), RiskLevel.HIGH_RISK),
        (BloodPressure(140, 85), SymptomSet(), RiskLevel.HIGH_RISK),
        # Normal BP + symptoms -> observation
        (BloodPressure(120, 80), SymptomSet(headache=True), RiskLevel.OBSERVATION),
        # Normal BP, no symptoms -> routine
        (BloodPressure(120, 80), SymptomSet(), RiskLevel.ROUTINE),
        (BloodPressure(118, 76), SymptomSet(), RiskLevel.ROUTINE),
    ],
)
def test_triage_matrix_matches_the_bidar_protocol(bp, symptoms, expected):
    assert TRIAGE.assess(bp, symptoms).level is expected


def test_severe_range_bp_escalates_on_the_reading_alone():
    """>=160/110 is a referral under standard obstetric practice even with no
    symptoms reported - an addition on top of the spec matrix. Since
    2026-09-10 this is subsumed by the hypertensive-alone rule (any BP
    >=140/90 is HIGH_RISK), but is kept as its own assertion since a severe
    reading being HIGH_RISK is true independent of that later change."""
    assert TRIAGE.assess(BloodPressure(165, 100), SymptomSet()).level is RiskLevel.HIGH_RISK
    assert TRIAGE.assess(BloodPressure(150, 115), SymptomSet()).level is RiskLevel.HIGH_RISK


def test_severe_rule_flag_no_longer_changes_the_outcome():
    """`escalate_on_severe_bp_alone=False` used to drop a severe-alone
    reading to MODERATE. Since 2026-09-10, plain hypertensive-alone (which a
    severe reading always also is) is HIGH_RISK unconditionally, so this
    flag no longer has an observable effect - see the constructor's own
    comment in sakhi_triage.py, which discloses this rather than leaving the
    flag silently misleading. Renamed from
    test_severe_rule_can_be_disabled_for_a_deployment, which asserted the
    now-superseded MODERATE outcome."""
    engine = TriageEngine(escalate_on_severe_bp_alone=False)
    assert engine.assess(BloodPressure(165, 100), SymptomSet()).level is RiskLevel.HIGH_RISK


def test_missing_bp_is_not_treated_as_normal():
    """Incremental triage: no reading yet must not read as 'BP is fine'."""
    result = TRIAGE.assess(None, SymptomSet(headache=True))
    assert result.level is RiskLevel.OBSERVATION
    assert any("not yet recorded" in r for r in result.reasons)


def test_assessment_carries_reasons_for_the_audit_trail():
    result = TRIAGE.assess(BloodPressure(150, 95), SymptomSet(headache=True))
    assert result.requires_escalation
    assert any("150/95" in r for r in result.reasons)
    assert any("headache" in r for r in result.reasons)
    assert "Bidar District Hospital" in result.recommended_action


def test_early_gestation_high_bp_is_flagged_as_possibly_chronic():
    result = TRIAGE.assess(BloodPressure(150, 95), SymptomSet(), gestational_weeks=14)
    assert any("chronic hypertension" in r for r in result.reasons)


def test_observation_does_not_escalate():
    """Only HIGH_RISK auto-connects a specialist.

    Renamed from test_moderate_does_not_escalate (2026-09-10): the case it
    used (BloodPressure(145, 95), no symptoms) is now HIGH_RISK per the
    hypertensive-alone calibration change - see sakhi_triage.py's module
    docstring. RiskLevel.MODERATE is no longer reachable at all, so this now
    uses a normal-BP-plus-symptom case (OBSERVATION) to guard the same
    underlying rule: only HIGH_RISK requires escalation.
    """
    assert not TRIAGE.assess(BloodPressure(120, 80), SymptomSet(headache=True)).requires_escalation


# --------------------------------------------------------------------------
# CallState
# --------------------------------------------------------------------------

def test_identify_only_fills_blanks_and_never_wipes_a_confirmed_name():
    state = SakhiCallState()
    state.identify(name="Suma Gowda")
    state.identify(name="umm")          # STT noise on a later turn
    assert state.name == "Suma Gowda"


def test_identify_normalises_phone_shapes():
    state = SakhiCallState()
    state.identify(phone="+91 98765-43210")
    assert state.phone == "9876543210"


def test_identify_rejects_implausible_clinical_values():
    state = SakhiCallState()
    state.identify(age=200, gestational_weeks=99)
    assert state.age is None and state.gestational_weeks is None


def test_prompt_summary_tells_the_model_what_not_to_re_ask():
    state = SakhiCallState()
    summary = state.prompt_summary()
    # The old imperatives ("NOT TAKEN - ask for it") were removed: injected on
    # every turn ahead of the intake line, they made the model ask for BP on
    # turn one. Status is reported; only the intake line says what to ask.
    assert "UNKNOWN" in summary
    assert "bp=not yet recorded" in summary and "symptoms=not yet screened" in summary
    assert "ask for it" not in summary, "a per-turn imperative is back; it will pre-empt the flow"

    state.identify(name="Priya Patil", gestational_weeks=30)
    state.record_blood_pressure(BloodPressure(145, 95))
    state.record_symptoms(SymptomSet(headache=True))
    summary = state.prompt_summary()
    assert "do NOT ask again" in summary and "do NOT re-screen" in summary


@pytest.mark.parametrize("raw,expected", [
    ("  Suma  Gowda ", "Suma Gowda"),
    ("umm", None),
    ("hello", None),
    ("12345", None),
    ("", None),
    (None, None),
    ("ಸುಮಾ", "ಸುಮಾ"),          # Kannada script must pass
    ("प्रिया", "प्रिया"),          # Devanagari must pass
])
def test_clean_person_name(raw, expected):
    assert clean_person_name(raw) == expected


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("9-8-7-6-5-4-3-2-1-0", "9876543210"),   # seeded mock format
    ("+91 98765 43210", "9876543210"),
    ("098765 43210", "9876543210"),
    (None, None),
])
def test_normalize_phone_unifies_every_stored_and_spoken_shape(raw, expected):
    assert normalize_phone(raw) == expected


def test_legacy_risk_strings_map_onto_the_enum():
    """Seeded records use NORMAL/HIGH; the domain uses ROUTINE/HIGH_RISK."""
    assert PatientRecord.from_dict({"risk_status": "NORMAL"}).risk_status is RiskLevel.ROUTINE
    assert PatientRecord.from_dict({"risk_status": "HIGH"}).risk_status is RiskLevel.HIGH_RISK
    assert PatientRecord.from_dict({"risk_status": "HIGH_RISK"}).risk_status is RiskLevel.HIGH_RISK
    assert PatientRecord.from_dict({}).risk_status is RiskLevel.ROUTINE


@pytest.fixture
def repo(tmp_path):
    """A repository over a throwaway seed file - never the real one."""
    import json
    path = tmp_path / "db.json"
    path.write_text(json.dumps({
        "patients": [{
            "patient_id": "M-201", "name": "Suma Gowda", "age": 24,
            "village": "Kamalnagar", "phone": "9-8-7-6-5-4-3-2-1-0",
            "risk_status": "NORMAL", "history": [], "symptoms": {},
        }],
        "bp_records": [], "teleconsult_logs": [],
    }), encoding="utf-8")
    return MockSixtyDBRepository(path, flush_delay=0.01)


def _run(coro):
    return asyncio.run(coro)


def test_find_by_name_and_by_legacy_dashed_phone(repo):
    async def go():
        await repo.load()
        assert (await repo.find_patients(name="suma"))[0].patient_id == "M-201"
        assert (await repo.find_patients(phone="9876543210"))[0].patient_id == "M-201"
        assert await repo.find_patients(name="nobody") == []
        assert await repo.find_patients() == []   # no criteria -> no match
    _run(go())


def test_new_patient_ids_never_collide_after_a_deletion(repo):
    """The old scheme was `M-{len(patients)+201}`, which reissues a live id as
    soon as any record is removed."""
    async def go():
        await repo.load()
        first = await repo.create_patient(name="A")
        second = await repo.create_patient(name="B")
        assert first.patient_id != second.patient_id
        repo._data["patients"].pop(0)          # simulate a removal
        third = await repo.create_patient(name="C")
        assert third.patient_id not in {first.patient_id, second.patient_id}
    _run(go())


def test_risk_status_ratchets_and_never_downgrades(repo):
    """A later normal reading must not clear a red alert already acted on."""
    async def go():
        await repo.load()
        high = TRIAGE.assess(BloodPressure(150, 100), SymptomSet(headache=True))
        await repo.record_blood_pressure("M-201", BloodPressure(150, 100), high)
        assert (await repo.get_patient("M-201")).risk_status is RiskLevel.HIGH_RISK

        normal = TRIAGE.assess(BloodPressure(118, 76), SymptomSet())
        await repo.record_blood_pressure("M-201", BloodPressure(118, 76), normal)
        assert (await repo.get_patient("M-201")).risk_status is RiskLevel.HIGH_RISK
    _run(go())


def test_symptoms_merge_into_the_stored_record(repo):
    async def go():
        await repo.load()
        await repo.record_symptoms("M-201", SymptomSet(headache=True))
        await repo.record_symptoms("M-201", SymptomSet(swelling=True))
        stored = (await repo.get_patient("M-201")).symptoms
        assert stored.headache and stored.swelling
    _run(go())


def test_teleconsult_is_appended_to_the_audit_trail(repo):
    async def go():
        await repo.load()
        assessment = TRIAGE.assess(BloodPressure(150, 100), SymptomSet(headache=True))
        log_id = await repo.log_teleconsult("M-201", "pre-eclampsia red alert", assessment)
        logs = repo._data["teleconsult_logs"]
        assert len(logs) == 1
        assert logs[0]["log_id"] == log_id
        assert logs[0]["risk_level"] == "HIGH_RISK"
        assert logs[0]["reasons"]           # reasons captured for clinical review
    _run(go())


def test_writes_survive_a_reload(repo, tmp_path):
    """Write-behind must actually reach disk by close()."""
    async def go():
        await repo.load()
        created = await repo.create_patient(name="Lakshmi", age=27, village="Aurad")
        await repo.close()

        fresh = MockSixtyDBRepository(repo._path, flush_delay=0.01)
        await fresh.load()
        assert (await fresh.get_patient(created.patient_id)).name == "Lakshmi"
    _run(go())


def test_missing_seed_file_starts_empty_instead_of_crashing(tmp_path):
    async def go():
        empty = MockSixtyDBRepository(tmp_path / "does_not_exist.json")
        await empty.load()
        assert await empty.find_patients(name="anyone") == []
    _run(go())
