"""Three bugs that made Sakhi go silent on a live call (18 Aug 2026).

Symptom: the greeting played, then Sakhi never spoke again. The ASHA worker
said "Hello. Are you there?" three times and got nothing.
"""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("SARVAM_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import agent
from livekit.agents.llm import ChatContext
from sakhi_domain import SakhiCallState
from sakhi_prompts import SAKHI_SYSTEM_PROMPT
from sakhi_sql_repository import SQLitePatientRepository
from sakhi_tools import ClinicalToolset
from sakhi_triage import TRIAGE

CLINICAL = {"search_patient", "register_mother", "record_bp",
            "record_symptoms", "connect_smfmi_specialist"}


def _run(coro):
    return asyncio.run(coro)


class _Info:
    def __init__(self, name):
        self.name = name


class _RegisteredTool:
    """Mirrors LiveKit's FunctionTool: .info.name, and NO __name__."""

    def __init__(self, name):
        self.info = _Info(name)


# --- 1. duplicate function name killed every turn --------------------------

def test_tool_name_reads_both_shapes():
    """tools_for() returns bound methods; Agent.tools returns FunctionTools.

    Reading only __name__ returned "" for the registered objects, so the
    clinical filter matched nothing and every tool was appended twice.
    """
    assert agent._tool_name(_RegisteredTool("search_patient")) == "search_patient"

    def search_patient():
        ...

    assert agent._tool_name(search_patient) == "search_patient"


def test_rescoping_never_produces_a_duplicate_tool():
    """LiveKit raises ValueError('duplicate function name') and the turn dies."""
    async def go():
        repo = await SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load()
        toolset = ClinicalToolset(repo, TRIAGE)
        scoped = toolset.tools_for(SakhiCallState())

        registered = [_RegisteredTool(n) for n in
                      ("search_patient", "register_mother", "record_bp",
                       "record_symptoms", "end_call")]
        extras = [t for t in registered if agent._tool_name(t) not in CLINICAL]
        final = [*scoped, *extras]

        names = [agent._tool_name(t) for t in final]
        assert len(names) == len(set(names)), f"duplicate tool: {names}"
        await repo.close()
    _run(go())


def test_rescoping_preserves_end_call():
    """Re-scoping must never strip the model's ability to hang up."""
    async def go():
        repo = await SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load()
        toolset = ClinicalToolset(repo, TRIAGE)
        scoped = toolset.tools_for(SakhiCallState())
        registered = [_RegisteredTool("search_patient"), _RegisteredTool("end_call")]
        extras = [t for t in registered if agent._tool_name(t) not in CLINICAL]
        names = [agent._tool_name(t) for t in [*scoped, *extras]]
        assert "end_call" in names
        await repo.close()
    _run(go())


# --- 2. context pruning deleted the system prompt --------------------------

def _prune(ctx, max_items=12, max_tokens=700):
    """The production pruning rule, extracted for test."""
    system_items = [i for i in ctx.items if getattr(i, "role", None) == "system"]
    conversation = [i for i in ctx.items if getattr(i, "role", None) != "system"]

    def toks():
        probe = ctx.copy()
        probe.items = list(conversation)
        return agent._approx_context_tokens(probe)

    while conversation and (len(conversation) > max_items or toks() > max_tokens):
        conversation.pop(0)
    ctx.items = [*system_items, *conversation]
    return ctx


def test_system_prompt_survives_aggressive_pruning():
    """A live call pruned 6 items/~2324 tok to 5 items/~92 tok - the prompt
    itself. From that turn on Sakhi had no persona and no intake control."""
    ctx = ChatContext()
    ctx.add_message(role="system", content=SAKHI_SYSTEM_PROMPT)
    for _ in range(25):
        ctx.add_message(role="user", content="ಅವಳ ಹೆಸರು ಸುಮಾ ಗೌಡ " * 40)

    _prune(ctx)

    systems = [i for i in ctx.items if getattr(i, "role", None) == "system"]
    assert systems, "the system prompt was pruned away"
    text = " ".join(str(getattr(i, "text_content", "") or "") for i in systems)
    assert "Sakhi" in text


def test_pruning_still_bounds_the_conversation():
    """The prompt is exempt; runaway transcripts must still be trimmed."""
    ctx = ChatContext()
    ctx.add_message(role="system", content=SAKHI_SYSTEM_PROMPT)
    for _ in range(40):
        ctx.add_message(role="user", content="ಅವಳ ಹೆಸರು ಸುಮಾ ಗೌಡ " * 40)

    _prune(ctx)

    conversation = [i for i in ctx.items if getattr(i, "role", None) != "system"]
    assert len(conversation) <= 12, "conversation was not bounded"


def test_pruning_a_short_call_changes_nothing():
    ctx = ChatContext()
    ctx.add_message(role="system", content="short prompt")
    ctx.add_message(role="user", content="her name is Suma")
    before = len(ctx.items)
    _prune(ctx)
    assert len(ctx.items) == before


# --- 3. repository teardown raised on every call ---------------------------

def test_sqlite_repository_implements_flush():
    """agent.py calls flush() at the end of EVERY call; its absence raised
    AttributeError on every teardown."""
    async def go():
        repo = await SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load()
        await repo.flush()
        await repo.flush()          # idempotent
        await repo.close()
    _run(go())


def test_flush_is_safe_after_close():
    async def go():
        repo = await SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load()
        await repo.close()
        await repo.flush()          # must not raise
    _run(go())
