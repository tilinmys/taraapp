"""Spelled-name recovery — the ASR fallback that was a dead end.

`assemble_spelled_name` was deleted with the English clinic fast path. That
left the spelling flow broken end to end: the tools asked the ASHA worker to
spell the name, she spelled it, and `search_patient` then looked up the literal
string "S, U, M, A" and found nothing. She had done exactly what was asked and
got the same failure.
"""

import asyncio
import json
import tempfile
import types
from pathlib import Path

import pytest

from sakhi_domain import SakhiCallState
from sakhi_prompts import SAKHI_SYSTEM_PROMPT
from sakhi_repository import assemble_spelled_name, looks_spelled, spell_out
from sakhi_sql_repository import SQLitePatientRepository
from sakhi_tools import ClinicalToolset
from sakhi_triage import TRIAGE


def _run(coro):
    return asyncio.run(coro)


# --- detection -------------------------------------------------------------

@pytest.mark.parametrize("spoken", [
    "S, U, M, A", "S. U. M. A.", "s u m a", "P, R, I, Y, A", "S,U,M,A",
])
def test_spelled_forms_are_recognised(spoken):
    assert looks_spelled(spoken), f"{spoken!r} not recognised as spelling"


@pytest.mark.parametrize("spoken", [
    "Suma Gowda", "Priya Patil", "ಸುಮಾ ಗೌಡ", "her name is Suma", "Suma",
])
def test_ordinary_names_are_not_mistaken_for_spelling(spoken):
    """A normal name must pass through untouched, or every lookup breaks."""
    assert not looks_spelled(spoken)
    assert assemble_spelled_name(spoken) is None


# --- assembly --------------------------------------------------------------

@pytest.mark.parametrize("spoken,expected", [
    ("S, U, M, A", "SUMA"),
    ("S. U. M. A.", "SUMA"),
    ("s u m a", "suma"),
    ("P, R, I, Y, A", "PRIYA"),
])
def test_letters_assemble_into_a_name(spoken, expected):
    assert assemble_spelled_name(spoken) == expected


def test_case_is_preserved_for_downstream_matching():
    """match_key() handles script and vowel-drop variation; forcing case here
    would mangle Kannada letters."""
    assert assemble_spelled_name("s u m a") == "suma"


# --- echo-back formatting --------------------------------------------------

def test_spell_out_separates_letters_with_commas():
    """The commas are load-bearing: TTS is clause-buffered and treats
    punctuation as a pause, which is what makes letters land separately."""
    assert spell_out("Priya") == "P, R, I, Y, A"
    assert spell_out("Suma Gowda") == "S, U, M, A, G, O, W, D, A"


# --- the whole flow --------------------------------------------------------

@pytest.fixture
def toolset():
    async def go():
        repo = await SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load()
        await repo.create_patient(name="Suma Gowda", age=24, village="Aurad",
                                  gestational_weeks=28)
        return ClinicalToolset(repo, TRIAGE)
    return _run(go())


@pytest.mark.parametrize("spelled", ["S, U, M, A", "S. U. M. A.", "s u m a"])
def test_a_spelled_name_now_finds_the_mother(toolset, spelled):
    """This is the bug: every one of these returned found=False."""
    async def go():
        state = SakhiCallState()
        ctx = types.SimpleNamespace(userdata=state)
        result = json.loads(await toolset.search_patient(ctx, name=spelled))
        assert result.get("found") is True, f"{spelled!r} still does not resolve"
    _run(go())


def test_a_normal_name_still_resolves(toolset):
    async def go():
        state = SakhiCallState()
        ctx = types.SimpleNamespace(userdata=state)
        result = json.loads(await toolset.search_patient(ctx, name="Suma Gowda"))
        assert result.get("found") is True
    _run(go())


def test_a_genuinely_absent_mother_is_still_absent(toolset):
    """The assembler must not turn every miss into a false match."""
    async def go():
        state = SakhiCallState()
        ctx = types.SimpleNamespace(userdata=state)
        result = json.loads(await toolset.search_patient(ctx, name="Lakshmi Bai"))
        assert result.get("found") is not True
    _run(go())


# --- prompt rules ----------------------------------------------------------

def test_prompt_forbids_re_asking_the_name():
    assert "can you repeat the name" in SAKHI_SYSTEM_PROMPT


def test_prompt_refuses_to_guess_clinical_values():
    """Inferring a garbled NAME from context is fine. Inferring a blood
    pressure is a fabricated clinical record."""
    assert "NEVER infer a clinical value" in SAKHI_SYSTEM_PROMPT
