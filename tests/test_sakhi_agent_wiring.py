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
from llm_hedge import HedgedLLM  # noqa: E402
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
    #
    # `[VAD:` used to be on this list, back when the tag protocol had been
    # removed. It is deliberately back: the tags are a better endpointing signal
    # than guessing from the agent's wording. What made them dangerous was never
    # their presence in the prompt but that they could be SPOKEN, and that the
    # old handler mutated the process-shared VAD. Both are covered by their own
    # tests below.
    for leaked in [
        "MyStree Clinic", "CLOSED on Sundays", "receptionist",
        "appointment", "Gracy", "Malayalam",
    ]:
        assert leaked not in text, f"clinic prompt leftover still present: {leaked}"


def test_prompt_carries_the_language_honorific():
    kn = agent.build_initial_context(language=Language.KANNADA)
    hi = agent.build_initial_context(language=Language.HINDI)
    assert "ಅಕ್ಕಾರ" in " ".join(m.text_content or "" for m in kn.items)
    assert "दीदी" in " ".join(m.text_content or "" for m in hi.items)


# --------------------------------------------------------------------------
# VAD tag protocol — the tag must steer endpointing and never be spoken
# --------------------------------------------------------------------------

class _FakeController:
    def __init__(self):
        self.applied = []

    def apply_tag(self, tag):
        self.applied.append(tag.upper())


async def _drive(transform, chunks):
    async def src():
        for c in chunks:
            yield c
    return "".join([c async for c in transform(src())])


@pytest.mark.parametrize("chunks,expected", [
    (["Her BP? ", "[VAD: FAST]"], "FAST"),
    (["Describe symptoms ", "[VAD:", " SLOW]"], "SLOW"),          # split across chunks
    (["Ok ", "[", "VAD", ": ", "FAST", "]"], "FAST"),             # heavily fragmented
    (["Which village? [vad:slow]"], "SLOW"),                      # lowercase, no spaces
])
def test_vad_tag_is_routed_and_never_spoken(chunks, expected):
    controller = _FakeController()
    transform = agent.VadTagTransform()
    transform.controller = controller
    spoken = _run(_drive(transform, chunks))

    assert controller.applied == [expected]
    assert "VAD" not in spoken and "[" not in spoken, f"tag leaked to TTS: {spoken!r}"


def test_text_without_a_tag_passes_through_untouched():
    transform = agent.VadTagTransform()
    transform.controller = _FakeController()
    original = "ಸಾಮಾನ್ಯ ವಾಕ್ಯ, ಟ್ಯಾಗ್ ಇಲ್ಲ."
    assert _run(_drive(transform, [original])) == original


def test_vad_transform_survives_a_missing_controller():
    """Transforms are constructed before session.start() attaches the
    controller. A tag arriving in that window must not crash the turn."""
    transform = agent.VadTagTransform()          # controller is None
    spoken = _run(_drive(transform, ["Hello [VAD: FAST]"]))
    assert "VAD" not in spoken


def test_tag_maps_to_the_documented_endpointing_states():
    from unittest.mock import MagicMock
    controller = agent.DynamicVADController(MagicMock())
    controller.apply_tag("SLOW")
    assert controller.state == "HIGH_COGNITIVE_LOAD"
    controller.apply_tag("FAST")
    assert controller.state == "TRANSACTIONAL"
    controller.apply_tag("nonsense")             # unknown tag is ignored
    assert controller.state == "TRANSACTIONAL"


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
    assert "symptoms=not yet screened" in summary   # status, not an order to ask


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
# Context pruning
# --------------------------------------------------------------------------

def _ctx_with_history(turns: int):
    """A context shaped like a real call: system prompt, then user/assistant
    pairs, with a tool call and its result in the middle."""
    from livekit.agents.llm import chat_context as cc

    ctx = agent.llm.ChatContext()
    ctx.add_message(role="system", content="SYSTEM PROMPT MARKER")
    for i in range(turns):
        ctx.add_message(role="user", content=f"user turn {i}")
        if i == turns // 2:
            ctx.insert(cc.FunctionCall(call_id=f"c{i}", name="record_bp", arguments="{}"))
            ctx.insert(cc.FunctionCallOutput(call_id=f"c{i}", name="record_bp", output="{}", is_error=False))
        ctx.add_message(role="assistant", content=f"assistant turn {i}")
    return ctx


def test_pruning_keeps_the_system_prompt():
    """The system prompt is the whole persona and safety boundary - losing it
    to a sliding window would be catastrophic and silent."""
    ctx = _ctx_with_history(20)
    ctx.truncate(max_items=12)
    text = " ".join(m.text_content or "" for m in ctx.items if m.type == "message")
    assert "SYSTEM PROMPT MARKER" in text


def test_pruning_never_orphans_a_tool_result():
    """A function_call_output whose originating function_call was cut is
    rejected outright by the OpenAI-compatible API - the turn dies."""
    for max_items in range(2, 20):
        ctx = _ctx_with_history(12)
        ctx.truncate(max_items=max_items)
        items = [i for i in ctx.items if i.type != "message"]
        open_calls = set()
        for item in items:
            if item.type == "function_call":
                open_calls.add(item.call_id)
            elif item.type == "function_call_output":
                assert item.call_id in open_calls, (
                    f"orphaned tool result at max_items={max_items}"
                )


def test_pruning_bounds_growth():
    ctx = _ctx_with_history(50)
    before = len(ctx.items)
    ctx.truncate(max_items=12)
    assert len(ctx.items) <= 13          # 12 + reinserted system prompt
    assert len(ctx.items) < before


def test_short_context_is_left_alone():
    ctx = _ctx_with_history(2)
    before = len(ctx.items)
    ctx.truncate(max_items=12)
    assert len(ctx.items) == before


# --------------------------------------------------------------------------
# Groq-only LLM chain
# --------------------------------------------------------------------------

def test_llm_chain_with_groq_keys(monkeypatch):
    """With Groq keys configured and no OpenRouter, builds Groq tiers."""
    monkeypatch.setenv("GROQ_API_KEYS", "key-one,key-two")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.delenv("VOICE_LLM_OPENAI_SAFETY_NET", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    chain = agent.build_llm(temperature=0.15, language=Language.KANNADA)

    providers = getattr(chain, "_llm", None) or getattr(chain, "_llm_instances", [])
    assert len(providers) == 4, f"expected 4 tiers, got {len(providers)}"
    for p in providers:
        base = str(getattr(p, "_client", None) and getattr(p._client, "base_url", "")) or ""
        assert "groq.com" in base


def test_openrouter_chain_wiring(monkeypatch):
    """With OpenRouter configured, builds fast OpenRouter primary and backup tiers."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_LLM_OPENAI_SAFETY_NET", raising=False)
    chain = agent.build_llm(temperature=0.15, language=Language.KANNADA)

    providers = getattr(chain, "_llm", None) or getattr(chain, "_llm_instances", [])
    assert len(providers) == 2, f"expected 2 OpenRouter tiers, got {len(providers)}"
    for p in providers:
        base = str(getattr(p, "_client", None) and getattr(p._client, "base_url", "")) or ""
        assert "openrouter.ai" in base


def _members(node):
    return getattr(node, "_llm_instances", None) or getattr(node, "_llm", None) or []


def _base_url(p):
    client = getattr(p, "_client", None)
    return str(getattr(client, "base_url", "")) if client else ""


def test_multi_provider_chain_is_grouped_and_hedged(monkeypatch):
    """Both vendors configured -> two per-vendor groups, raced not queued.

    The chain used to be six flat tiers behind ONE global attempt_timeout,
    with OpenRouter at tier 1. OpenRouter's median TTFT (1842ms) exceeded that
    timeout (1800ms), so tier 1 was cancelled on essentially every turn and
    every turn paid ~3.6s of fallback before reaching Groq's 604ms. The
    structure is now: HedgedLLM(groq_group, openrouter_group).
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("GROQ_API_KEYS", "groq-key-1,groq-key-2")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_LLM_OPENAI_SAFETY_NET", raising=False)
    monkeypatch.delenv("SAKHI_LLM_HEDGE", raising=False)

    chain = agent.build_llm(temperature=0.15, language=Language.KANNADA)

    assert isinstance(chain, HedgedLLM), (
        f"expected a hedged chain, got {type(chain).__name__}; serial groups "
        "cost ~3.7s on a total Groq outage"
    )

    groq_group, openrouter_group = chain._primary, chain._backup

    groq_tiers = _members(groq_group)
    assert len(groq_tiers) == 4
    assert all("groq.com" in _base_url(p) for p in groq_tiers), (
        "the FAST provider must lead; putting OpenRouter first is the original bug"
    )

    or_tiers = _members(openrouter_group)
    assert len(or_tiers) == 2
    assert all("openrouter.ai" in _base_url(p) for p in or_tiers)


def test_each_group_gets_its_own_timeout_budget(monkeypatch):
    """One global timeout cannot fit a 604ms provider and an 1842ms one."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("GROQ_API_KEYS", "groq-key-1,groq-key-2")
    monkeypatch.setenv("GROQ_ATTEMPT_TIMEOUT", "1.5")
    monkeypatch.setenv("OPENROUTER_ATTEMPT_TIMEOUT", "4.0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    chain = agent.build_llm(temperature=0.15, language=Language.KANNADA)

    assert chain._primary._attempt_timeout == 1.5
    assert chain._backup._attempt_timeout == 4.0
    assert chain._backup._attempt_timeout > chain._primary._attempt_timeout, (
        "the slower vendor needs the larger budget, or it can never answer"
    )


def test_hedging_can_be_disabled(monkeypatch):
    """Escape hatch: fall back to strictly serial groups."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("GROQ_API_KEYS", "groq-key-1,groq-key-2")
    monkeypatch.setenv("SAKHI_LLM_HEDGE", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    chain = agent.build_llm(temperature=0.15, language=Language.KANNADA)
    assert not isinstance(chain, HedgedLLM)
    assert len(_members(chain)) == 2, "expected two serial vendor groups"


def test_llm_chain_raises_without_any_key(monkeypatch):
    """Better to fail at build time than to answer a call with no LLM."""
    monkeypatch.setenv("GROQ_API_KEYS", "")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No LLM API key"):
        agent.build_llm(temperature=0.15, language=Language.KANNADA)


def test_single_groq_key_still_builds_all_tiers(monkeypatch):
    """One key must not silently shrink the chain - model diversity still
    protects against a single model being degraded."""
    monkeypatch.setenv("GROQ_API_KEYS", "only-key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    chain = agent.build_llm(temperature=0.15, language=Language.KANNADA)
    providers = getattr(chain, "_llm", None) or getattr(chain, "_llm_instances", [])
    assert len(providers) == 4


# --------------------------------------------------------------------------
# Voice catalog
# --------------------------------------------------------------------------

def test_catalog_default_needs_no_runtime_remap():
    """The catalog default was a bulbul:v3 speaker while the pilot runs v2, so
    every call logged a remap warning and fell back anyway."""
    import voice_catalog
    from sarvam_wrappers import SARVAM_DEFAULT_MODEL, resolve_sarvam_voice

    default = voice_catalog.default_voice("sarvam")
    assert default == "manisha"
    model, speaker = resolve_sarvam_voice(SARVAM_DEFAULT_MODEL, default)
    assert speaker == default, "catalog default is still being remapped at runtime"


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


# --------------------------------------------------------------------------
# English Language & Voice Stability Tests
# --------------------------------------------------------------------------

def test_english_system_prompt():
    from sakhi_prompts import system_prompt_for, SAKHI_SYSTEM_PROMPT_EN
    prompt = system_prompt_for(Language.ENGLISH)
    assert prompt == SAKHI_SYSTEM_PROMPT_EN
    assert "English" in prompt
    assert "Didi" in prompt
    assert "Safety Guardrail" in prompt


def test_english_greeting_and_goodbye():
    assert "Hello, I am Sakhi" in agent.greeting_for(Language.ENGLISH)
    assert "Thank you Didi" in agent.goodbye_for(Language.ENGLISH)
    assert "Sorry Didi" in agent.retry_prompt_for(Language.ENGLISH)


def test_english_stt_and_tts(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    monkeypatch.setenv("STT_PRIMARY", "sarvam")
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "0")
    stt_inst = agent.build_stt(Language.ENGLISH)
    assert getattr(stt_inst, "_language_code", None) == "en-IN"
    tts_inst = agent.build_tts(language=Language.ENGLISH)
    assert getattr(tts_inst, "_target_language_code", None) == "en-IN"
