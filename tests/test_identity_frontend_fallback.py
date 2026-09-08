"""Typed name/age fallback: when a voice lookup fails once, Sakhi should show
the ASHA worker a text box instead of burning more LLM/STT turns asking her
to spell it. Covers the pieces that live in sakhi_tools.py/sakhi_domain.py -
the RPC handler itself (agent.py) is a thin wrapper around
state.lock_identity_from_frontend() plus a repository find/create, exercised
manually over a live call per the plan's verification section.
"""

import asyncio
import tempfile
import types
from pathlib import Path

import pytest

from sakhi_domain import SakhiCallState
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


def _ctx(state):
    return types.SimpleNamespace(userdata=state)


# --- search_patient: first miss shows the box, not the spelling request ----

def test_first_miss_arms_the_box_instead_of_asking_to_spell(toolset):
    async def go():
        state = SakhiCallState()
        import json
        result = json.loads(await toolset.search_patient(_ctx(state), name="Anjaneya"))

        assert state.awaiting_identity_box is True
        assert "type the mother's name and age" in result["instruction"]
        assert "spell it letter by letter" not in result["instruction"]
    _run(go())


def test_second_search_patient_call_would_still_be_the_guest_fallback(toolset):
    """The lookup_attempts>=2 branch stays in place defensively - unreachable
    in the normal flow once tools_for() withdraws search_patient, but must
    not be deleted in case the tool is somehow invoked a third time."""
    async def go():
        state = SakhiCallState()
        await toolset.search_patient(_ctx(state), name="Anjaneya")
        import json
        result = json.loads(await toolset.search_patient(_ctx(state), name="Anjaneya"))
        assert "Guest Patient" in result["instruction"] or "fallback_name" in result
    _run(go())


# --- tools_for: search_patient withdrawn while the box is up ---------------

def test_tools_for_withdraws_search_patient_while_awaiting_box(toolset):
    state = SakhiCallState()
    state.awaiting_identity_box = True
    assert "search_patient" not in _names(toolset, state)
    # record_bp/record_symptoms must stay - out-of-order clinical facts must
    # never be droppable, box or no box.
    assert "record_bp" in _names(toolset, state)
    assert "record_symptoms" in _names(toolset, state)


def test_tools_for_restores_search_patient_once_resolved(toolset):
    state = SakhiCallState()
    state.awaiting_identity_box = True
    assert "search_patient" not in _names(toolset, state)

    state.lock_identity_from_frontend(name="Suma Gowda", age=24)
    assert state.awaiting_identity_box is False
    assert "search_patient" in _names(toolset, state)


# --- SakhiCallState.lock_identity_from_frontend -----------------------------

def test_lock_identity_from_frontend_sets_all_expected_fields():
    state = SakhiCallState()
    state.awaiting_identity_box = True

    state.lock_identity_from_frontend(name="Suma Gowda", age=24)

    assert state.name == "Suma Gowda"
    assert state.age == 24
    assert state.name_confirmed is True
    assert state.identity_locked is True
    assert state.awaiting_identity_box is False
    assert state.is_identified is True
