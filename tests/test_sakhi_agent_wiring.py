"""Agent-layer wiring tests for Sakhi.

Covers the seams between agent.py and the clinical core without needing
LiveKit network, API keys, or a room: prompt construction, per-turn state
injection, the deterministic escalation safety net, and the avatar seam.

Run:  python -m pytest tests/test_sakhi_agent_wiring.py -q
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# agent.py calls required_env() at build time for these.
os.environ.setdefault("SARVAM_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import agent  # noqa: E402
from sakhi_avatar import (  # noqa: E402
    BeyAvatarBackend,
    DIDAvatarBackend,
    NullAvatarBackend,
    TavusAvatarBackend,
    build_avatar_backend,
)
from sakhi_domain import BloodPressure, Language, RiskLevel, SakhiCallState, SymptomSet  # noqa: E402
from sakhi_repository import MockSixtyDBRepository  # noqa: E402
from sakhi_tools import ClinicalToolset  # noqa: E402
from sakhi_triage import TRIAGE  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "db.json"
    path.write_text(json.dumps({
        "patients": [{
            "patient_id": "M-201", "name": "Suma Gowda", "age": 24,
            "village": "Kamalnagar", "phone": "9876543210",
            "gestational_weeks": 28, "risk_status": "ROUTINE",
            "history": [], "symptoms": {},
        }],
        "bp_records": [], "teleconsult_logs": [],
    }), encoding="utf-8")
    return MockSixtyDBRepository(path, flush_delay=0.01)


# --------------------------------------------------------------------------
# System prompt
# --------------------------------------------------------------------------

def test_prompt_is_sakhi_not_the_clinic():
    ctx = agent.build_initial_context(language=Language.KANNADA)
    text = " ".join(m.text_content or "" for m in ctx.items)

    assert "Sakhi" in text
    # The clinic prompt's leftovers must all be gone - each was paid for in
    # prefill on every generation.
    for leaked in [
        "MyStree Clinic", "CLOSED on Sundays", "receptionist",
        "appointment", "Gracy", "Malayalam", "[VAD:",
    ]:
        assert leaked not in text, f"clinic prompt leftover still present: {leaked}"


def test_prompt_carries_the_language_honorific():
    kn = agent.build_initial_context(language=Language.KANNADA)
    hi = agent.build_initial_context(language=Language.HINDI)
    assert "ಅಕ್ಕಾರ" in " ".join(m.text_content or "" for m in kn.items)
    assert "दीदी" in " ".join(m.text_content or "" for m in hi.items)


def test_prompt_does_not_restate_triage_thresholds():
    """The engine owns the decision. Restating '>=140' invites the model to
    second-guess it."""
    text = " ".join(m.text_content or "" for m in agent.build_initial_context().items)
    assert "140" not in text and "90" not in text


# --------------------------------------------------------------------------
# Deterministic escalation safety net
# --------------------------------------------------------------------------

def _agent_with_state(toolset, state):
    sakhi = agent.SakhiAgent(
        instructions="t", chat_ctx=agent.llm.ChatContext(), toolset=toolset
    )
    session = MagicMock()
    session.userdata = state
    sakhi._activity = MagicMock(session=session)
    return sakhi


def test_escalation_pending_is_false_until_triage_says_high_risk(repo):
    async def go():
        await repo.load()
        toolset = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState(patient_id="M-201", name="Suma")
        sakhi = _agent_with_state(toolset, state)

        assert not sakhi._escalation_pending()          # nothing recorded yet

        state.record_blood_pressure(BloodPressure(145, 95))
        state.record_assessment(TRIAGE.assess(state.blood_pressure, state.symptoms))
        assert state.risk_level is RiskLevel.MODERATE
        assert not sakhi._escalation_pending()          # MODERATE must not escalate

        state.record_symptoms(SymptomSet(headache=True))
        state.record_assessment(TRIAGE.assess(state.blood_pressure, state.symptoms))
        assert state.risk_level is RiskLevel.HIGH_RISK
        assert sakhi._escalation_pending()              # now it must
    _run(go())


def test_escalation_fires_even_when_the_model_never_calls_the_tool(repo):
    """The safety property: a red alert must not depend on the LLM remembering
    a prompt instruction."""
    async def go():
        await repo.load()
        toolset = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState(patient_id="M-201", name="Suma")
        sakhi = _agent_with_state(toolset, state)

        state.record_blood_pressure(BloodPressure(150, 100))
        state.record_symptoms(SymptomSet(blurry_vision=True))
        state.record_assessment(
            TRIAGE.assess(state.blood_pressure, state.symptoms)
        )
        assert sakhi._escalation_pending()

        sakhi._force_escalation()
        await asyncio.sleep(0.05)      # let the spawned task run

        assert state.escalated, "specialist was never connected"
        assert not sakhi._escalation_pending()
        logs = repo._data["teleconsult_logs"]
        assert len(logs) == 1
        assert logs[0]["risk_level"] == "HIGH_RISK"
        assert logs[0]["reasons"], "audit entry must record why"
    _run(go())


def test_escalation_is_not_double_fired(repo):
    async def go():
        await repo.load()
        toolset = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState(patient_id="M-201")
        sakhi = _agent_with_state(toolset, state)
        state.record_blood_pressure(BloodPressure(170, 115))
        state.record_assessment(TRIAGE.assess(state.blood_pressure, state.symptoms))

        sakhi._force_escalation()
        sakhi._force_escalation()      # re-entrant call on the same turn
        await asyncio.sleep(0.05)
        sakhi._force_escalation()      # and again after it completed

        await asyncio.sleep(0.05)
        assert len(repo._data["teleconsult_logs"]) == 1
    _run(go())


def test_forced_escalation_never_raises_without_a_toolset():
    """A session missing its toolset must degrade, not crash a live call."""
    state = SakhiCallState()
    sakhi = _agent_with_state(None, state)
    sakhi._force_escalation()          # must be a no-op
    assert not state.escalated


# --------------------------------------------------------------------------
# Per-turn state injection
# --------------------------------------------------------------------------

def test_state_summary_reaches_the_model_every_turn():
    state = SakhiCallState(patient_id="M-201", name="Suma Gowda")
    state.record_blood_pressure(BloodPressure(118, 76))
    summary = state.prompt_summary()
    assert "Suma Gowda" in summary
    assert "118/76" in summary and "do NOT ask again" in summary
    assert "NOT SCREENED" in summary          # symptoms still outstanding


def test_state_summary_has_no_booking_fields():
    """The old CallState summary told the model to collect a doctor preference
    and confirm an appointment on every generation."""
    summary = SakhiCallState().prompt_summary()
    for leaked in ["doctor", "booking", "appointment"]:
        assert leaked not in summary.lower()


# --------------------------------------------------------------------------
# Avatar seam
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("none", NullAvatarBackend),
    ("tavus", TavusAvatarBackend),
    ("bey", BeyAvatarBackend),
    ("did", DIDAvatarBackend),
    ("nonsense", NullAvatarBackend),      # typo in a deploy var must not break calls
    ("TAVUS", TavusAvatarBackend),        # case-insensitive
])
def test_avatar_backend_selection(name, expected):
    assert isinstance(build_avatar_backend(name), expected)


def test_avatar_defaults_to_audio_only_when_env_is_unset(monkeypatch):
    """Explicitly isolate SAKHI_AVATAR: this repo's .env sets it to `tavus`, so
    asserting the no-argument default without clearing the variable tests the
    developer's machine rather than the code."""
    monkeypatch.delenv("SAKHI_AVATAR", raising=False)
    assert isinstance(build_avatar_backend(), NullAvatarBackend)


def test_avatar_follows_env_when_set(monkeypatch):
    monkeypatch.setenv("SAKHI_AVATAR", "tavus")
    assert isinstance(build_avatar_backend(), TavusAvatarBackend)


def test_tavus_uses_configured_replica(monkeypatch):
    monkeypatch.setenv("TAVUS_REPLICA_ID", "r_custom_bidar")
    backend = TavusAvatarBackend()
    assert "r_custom_bidar" in backend.description


@pytest.mark.parametrize("name", ["none", "tavus", "bey", "did", "nonsense"])
def test_no_avatar_backend_can_break_a_call(name):
    """start() must always return a bool and never raise, whatever the vendor
    does - voice is the product, video is an enhancement."""
    async def go():
        backend = build_avatar_backend(name)
        result = await backend.start(session=None, room=None)
        assert result is False          # none can succeed with null session/room
        await backend.aclose()          # must be safe when never started
    _run(go())


# --------------------------------------------------------------------------
# Turn handling / latency configuration
# --------------------------------------------------------------------------

def test_barge_in_is_actually_enabled():
    """Committed values were min_duration=5.0s / min_words=15, which disable
    barge-in rather than filtering backchannels."""
    interruption = agent.build_turn_handling(silence_threshold=0.3)["interruption"]
    assert interruption["min_duration"] <= 1.0, (
        f"barge-in still effectively disabled ({interruption['min_duration']}s)"
    )
    assert interruption["min_words"] <= 3, (
        f"barge-in still effectively disabled ({interruption['min_words']} words)"
    )


def test_endpointing_lands_in_the_target_band():
    """Target architecture specifies 250-350ms trailing silence."""
    endpointing = agent.build_turn_handling(silence_threshold=0.3)["endpointing"]
    assert 0.2 <= endpointing["min_delay"] <= 0.4
