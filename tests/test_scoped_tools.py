"""Tool scoping: rules the model cannot break because it cannot see the tool.

Prompt rules about tool ORDER are followed probabilistically. These two
failures were reproduced by calling the tools directly:

  - register_mother twice created M-201 AND M-202 for the same mother
  - connect_smfmi_specialist with no patient returned connected=True while the
    audit INSERT died on a foreign-key violation

Both are now structurally impossible rather than discouraged.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from sakhi_domain import BloodPressure, RiskLevel, SakhiCallState, SymptomSet
from sakhi_sql_repository import SQLitePatientRepository
from sakhi_tools import ClinicalToolset
from sakhi_triage import TRIAGE


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def toolset():
    repo = _run(SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load())
    return ClinicalToolset(repo, TRIAGE)


def _names(toolset, state):
    return [f.__name__ for f in toolset.tools_for(state)]


def _high_risk_state():
    state = SakhiCallState(patient_id="M-201", name="Suma Gowda")
    state.record_blood_pressure(BloodPressure(165, 115))
    state.record_symptoms(SymptomSet(headache=True))
    state.record_assessment(TRIAGE.assess(state.blood_pressure, state.symptoms))
    assert state.assessment.level is RiskLevel.HIGH_RISK
    return state


# --- registration cannot duplicate a mother --------------------------------

def test_register_is_offered_only_before_identification(toolset):
    assert "register_mother" in _names(toolset, SakhiCallState())


def test_register_is_withdrawn_once_identified(toolset):
    """The duplicate-record bug, removed as a possibility not an instruction."""
    state = SakhiCallState(patient_id="M-201", name="Suma Gowda")
    assert "register_mother" not in _names(toolset, state)


def test_search_survives_identification(toolset):
    """The worker may correct a misheard name and need a re-lookup."""
    state = SakhiCallState(patient_id="M-201", name="Suma Gowda")
    assert "search_patient" in _names(toolset, state)


# --- escalation is triage's decision, never the model's --------------------

def test_escalation_hidden_before_any_assessment(toolset):
    assert "connect_smfmi_specialist" not in _names(toolset, SakhiCallState())


def test_escalation_hidden_when_triage_says_observation(toolset):
    """Renamed from test_escalation_hidden_when_triage_says_moderate
    (2026-09-10): BP(145, 95) with no symptoms used to land at MODERATE. It
    is now HIGH_RISK on its own (an explicit user decision - see
    sakhi_triage.py's module docstring) and WOULD offer the escalation tool,
    so it can no longer serve as the non-escalating case this test needs.
    A symptomatic-but-normal-BP reading (OBSERVATION) is used instead -
    still a real, reachable non-escalating level, unlike MODERATE which is
    no longer reachable at all.
    """
    state = SakhiCallState(patient_id="M-201", name="Suma Gowda")
    state.record_assessment(TRIAGE.assess(None, SymptomSet(headache=True)))
    assert state.assessment.level is RiskLevel.OBSERVATION
    assert "connect_smfmi_specialist" not in _names(toolset, state)


def test_escalation_appears_only_on_high_risk(toolset):
    assert "connect_smfmi_specialist" in _names(toolset, _high_risk_state())


def test_escalation_withdrawn_after_it_has_happened(toolset):
    """Prevents a second page for the same mother on the same call."""
    state = _high_risk_state()
    state.escalated = True
    assert "connect_smfmi_specialist" not in _names(toolset, state)


def test_escalation_never_offered_without_a_patient(toolset):
    """This is what crashed the teleconsult audit write on a foreign key."""
    state = SakhiCallState()
    state.record_blood_pressure(BloodPressure(165, 115))
    state.record_symptoms(SymptomSet(headache=True))
    state.record_assessment(TRIAGE.assess(state.blood_pressure, state.symptoms))
    assert state.assessment.level is RiskLevel.HIGH_RISK
    assert "connect_smfmi_specialist" not in _names(toolset, state)


# --- but agility must survive ----------------------------------------------

@pytest.mark.parametrize("state_factory", [
    SakhiCallState,
    lambda: SakhiCallState(patient_id="M-201", name="Suma Gowda"),
    _high_risk_state,
])
def test_clinical_capture_is_available_in_every_phase(toolset, state_factory):
    """An ASHA worker reports facts out of order - "her BP is one sixty over
    one ten" before any name is known. Gating these would drop the reading."""
    names = _names(toolset, state_factory())
    assert "record_bp" in names
    assert "record_symptoms" in names


def test_no_phase_exposes_an_empty_surface(toolset):
    for state in (SakhiCallState(),
                  SakhiCallState(patient_id="M-201", name="S"),
                  _high_risk_state()):
        assert _names(toolset, state), "a phase with no tools deadlocks the call"


def test_as_list_still_returns_everything(toolset):
    """Initial Agent construction has no call state yet.

    6, not 5, since record_gestational_age (2026-08-30, Module B) joined the
    always-available set alongside record_bp/record_symptoms.
    """
    assert len(toolset.as_list()) == 9


# --- re-scoping merge cannot ever re-duplicate a clinical tool -------------
#
# Confirmed live (2026-09-03): agent.py merges a fresh tools_for(state) list
# with "extras" from the currently-attached Agent.tools (so end_call always
# survives a re-scope), filtering extras against a `clinical` name set. That
# set used to be hand-typed with 5 names and silently went stale the moment
# a 6th clinical tool (record_gestational_age) - and later 3 more - were
# added to ClinicalToolset: LiveKit's `llm.ToolContext` raised "duplicate
# function name" and crashed _pipeline_reply_task on every turn, killing the
# call. The real fix is agent.py deriving `clinical` from
# ClinicalToolset.as_list() itself rather than a copy of its names - this
# test reproduces that exact merge and asserts the invariant holds for every
# tool as_list() can ever produce, present or future.
def test_rescoping_merge_never_duplicates_a_clinical_tool(toolset):
    def tool_name(tool) -> str:
        info = getattr(tool, "info", None)
        name = getattr(info, "name", None) if info is not None else None
        return name or getattr(tool, "__name__", "") or repr(tool)

    clinical = {tool_name(t) for t in toolset.as_list()}
    # Simulates Agent.tools right after initial construction (as_list()) plus
    # one genuinely non-clinical tool (end_call) that must survive re-scoping.
    current_tools = [*toolset.as_list(), "end_call_stub"]

    for state in (SakhiCallState(),
                  SakhiCallState(patient_id="M-201", name="Suma Gowda"),
                  _high_risk_state()):
        scoped = toolset.tools_for(state)
        extras = [t for t in current_tools if tool_name(t) not in clinical]
        merged_names = [tool_name(t) for t in (*scoped, *extras)]
        assert len(merged_names) == len(set(merged_names)), (
            f"duplicate tool name in merged surface: {merged_names}"
        )
        assert "end_call_stub" in extras, "end_call must survive every re-scope"
