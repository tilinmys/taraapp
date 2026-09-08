"""Module B (2026-08-30): the brief asked for a record_clinical_batch(dict)
tool for multi-slot utterances like "BP 130/80, 7th month, mild leg
swelling". Recommended against, in the plan: no tool in this codebase takes
a dict/nested parameter, there's no precedent it works cleanly against
Groq/OpenRouter's function-calling, and the actual gap was narrower than the
brief assumed. record_bp and record_symptoms already handle their half of
that example turn via ordinary multi-tool-calls-per-turn (already asked for
in the prompt's "Dynamic Absorption" instruction) - the one real gap was
gestational age, previously only settable at registration time. This file
covers the new record_gestational_age tool and the multi-tool-call turn.
"""

import asyncio
import json
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


def _ctx(state):
    return types.SimpleNamespace(userdata=state)


# --- record_gestational_age -------------------------------------------

def test_record_gestational_age_sets_state(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        result = json.loads(await toolset.record_gestational_age(_ctx(state), 28))
        assert result["recorded"] is True
        assert state.gestational_weeks == 28
    _run(go())


def test_record_gestational_age_mid_call_after_registration(toolset):
    """The actual gap this tool closes: today gestational_weeks is only set
    inside register_mother/search_patient at registration time - a later
    mid-call mention had nowhere to land before this tool existed."""
    async def go():
        state = SakhiCallState(patient_id="M-201", gestational_weeks=None)
        assert state.gestational_weeks is None
        await toolset.record_gestational_age(_ctx(state), 30)
        assert state.gestational_weeks == 30
    _run(go())


@pytest.mark.parametrize("weeks", [0, -1, 46, 100])
def test_record_gestational_age_rejects_implausible_values(toolset, weeks):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        result = json.loads(await toolset.record_gestational_age(_ctx(state), weeks))
        assert result["recorded"] is False
        assert state.gestational_weeks is None
    _run(go())


def test_record_gestational_age_is_in_the_always_available_set(toolset):
    state = SakhiCallState()
    names = [f.__name__ for f in toolset.tools_for(state)]
    assert "record_gestational_age" in names
    # Same reasoning as record_bp/record_symptoms: out-of-order clinical
    # facts must never be droppable, box-awaiting or not.
    state.awaiting_identity_box = True
    names_while_awaiting = [f.__name__ for f in toolset.tools_for(state)]
    assert "record_gestational_age" in names_while_awaiting


# --- multi-slot turn: "BP 130/80, 7th month, mild leg swelling" --------
#
# This already works via ordinary multi-tool-calls-per-turn (the model
# calling three tools against the same ctx/state in one turn) - no new
# batch-extraction machinery needed. This test proves the combination.

# --- Deepgram medical-glossary keyword priming --------------------------

def test_deepgram_stt_is_primed_with_the_medical_glossary(monkeypatch):
    """build_stt()'s Deepgram path must actually pass the medical keyterm
    list through to the plugin - the whole point is dead without this."""
    monkeypatch.setenv("STT_PRIMARY", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram-key")
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    monkeypatch.setenv("VOICE_STT_FALLBACK_ENABLED", "0")

    import agent
    from sakhi_domain import Language

    stt_instance = agent.build_stt(Language.KANNADA)
    assert stt_instance._user_keyterm == agent.DEEPGRAM_MEDICAL_KEYTERMS
    assert "ರಕ್ತಸ್ರಾವ" in stt_instance._user_keyterm  # the Tier-1 addition


def test_multi_tool_call_turn_records_all_three_facts(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        ctx = _ctx(state)

        await toolset.record_bp(ctx, 130, 80)
        await toolset.record_gestational_age(ctx, 30)  # "7th month" ~ 30 weeks
        await toolset.record_symptoms(ctx, swelling=True)

        assert state.blood_pressure is not None
        assert state.blood_pressure.systolic == 130
        assert state.gestational_weeks == 30
        assert state.symptoms.swelling
    _run(go())
