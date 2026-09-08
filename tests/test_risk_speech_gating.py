"""Sakhi must not editorialize a non-emergency risk level before BP and
symptoms are both actually recorded - the mother is often sitting right next
to the ASHA worker, and a half-formed risk comment either alarms her over
nothing or gets contradicted a turn later once the full picture is in.

A genuine emergency (HIGH_RISK) is the opposite case: it must still fire
immediately, with no such gate, because delaying a real one is unsafe. This
file is the regression guard for both halves of that policy.
"""

import asyncio
import json
import tempfile
import types
from pathlib import Path

import pytest

from sakhi_domain import RiskLevel, SakhiCallState
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


# --- MODERATE/OBSERVATION: gated on BP + symptoms both being recorded ------
#
# 2026-09-10: the two tests that used to live here recorded a hypertensive
# BP alone (145/95) and asserted the resulting RiskLevel.MODERATE was held
# back from speech until the symptom screen completed. That scenario is now
# structurally unreachable - per an explicit user decision (see
# sakhi_triage.py's module docstring), any hypertensive reading is HIGH_RISK
# immediately, and HIGH_RISK is deliberately exempt from this gate (it must
# speak the instant it's known - see the "HIGH_RISK: unchanged, immediate,
# no gate" section below). RiskLevel.MODERATE is no longer produced by
# _classify() at all. In its place: a direct test that a plain hypertensive
# reading (not just a severe one) now also speaks immediately, which is the
# actual behavior this calibration change introduced.

def test_hypertensive_alone_now_speaks_immediately_not_gated(toolset):
    """2026-09-10: replaces the old "MODERATE reading says nothing until
    symptoms are screened" test. A plain hypertensive reading (145/95, not
    severe) used to be MODERATE and wait for the symptom screen; it is now
    HIGH_RISK and must speak immediately, the same as the pre-existing
    severe-BP-alone case below."""
    async def go():
        state = SakhiCallState(patient_id="M-201")
        result = json.loads(await toolset.record_bp(_ctx(state), 145, 95))
        assert state.risk_level is RiskLevel.HIGH_RISK
        assert not state.symptoms_screened, "test setup: symptoms must still be unscreened"

        instruction = result["instruction"]
        assert "Continue the intake normally" not in instruction, "emergency was gated - unsafe"
        assert "connect_smfmi_specialist" in instruction
        assert "urgent attention" in instruction
    _run(go())


def test_observation_reading_says_nothing_until_bp_is_recorded(toolset):
    """The gate is symmetric: symptoms-first should equally wait on BP."""
    async def go():
        state = SakhiCallState(patient_id="M-201")
        result = json.loads(await toolset.record_symptoms(_ctx(state), headache=True))
        assert state.risk_level is RiskLevel.OBSERVATION
        assert state.blood_pressure is None

        assert "Continue the intake normally" in result["instruction"]
    _run(go())


# --- HIGH_RISK: unchanged, immediate, no gate -------------------------------

def test_high_risk_reading_is_voiced_immediately_regardless_of_symptoms(toolset):
    """Severe BP alone is HIGH_RISK per TriageEngine - must escalate in speech
    the instant it's recorded, not wait for the symptom screen."""
    async def go():
        state = SakhiCallState(patient_id="M-201")
        result = json.loads(await toolset.record_bp(_ctx(state), 165, 115))
        assert state.risk_level is RiskLevel.HIGH_RISK
        assert not state.symptoms_screened, "test setup: symptoms must still be unscreened"

        instruction = result["instruction"]
        assert "Continue the intake normally" not in instruction, "emergency was gated - unsafe"
        assert "connect_smfmi_specialist" in instruction
        assert "urgent attention" in instruction
    _run(go())
