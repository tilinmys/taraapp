"""Side-talk gate, repeat handling, and the spelling loop ceiling.

The ASHA worker runs the call and the examination at once. She constantly
turns away to speak to the mother. Answering that side-talk burns an LLM
round trip, talks over her, and can make Sakhi respond to an instruction
meant for a patient.

Enforced in code rather than in the prompt: a prompt rule cannot suppress a
turn - the model still generates and the audio still plays. Only StopResponse
keeps Sakhi quiet.
"""

import asyncio
import json
import types

import pytest

import agent
from sakhi_domain import SakhiCallState
from sakhi_repository import MockSixtyDBRepository
from sakhi_tools import GUEST_PATIENT_NAME, ClinicalToolset
from sakhi_triage import TRIAGE


def _run(coro):
    return asyncio.run(coro)


# --- side-talk must be ignored --------------------------------------------

@pytest.mark.parametrize("text", [
    "Suma, sit down",
    "sit down and take a deep breath",
    "don't worry, it will not hurt",
    "roll up your sleeve",
    "ಸುಮಾ, ಕುಳಿತುಕೊಳ್ಳಿ",
    "ಉಸಿರು ತೆಗೆದುಕೊಳ್ಳಿ",
    "ಹೆದರಬೇಡಿ",
    "बैठ जाओ",
    "गहरी साँस लो",
    "डरो मत",
])
def test_patient_directed_speech_is_ignored(text):
    assert agent.is_side_talk(text), f"{text!r} would be answered; it is meant for the patient"


# --- but a real turn must NEVER be swallowed -------------------------------

@pytest.mark.parametrize("text", [
    "her BP is one forty over ninety",
    "her name is Suma Gowda",
    "she has a headache since Monday",
    "ಅವಳ ಹೆಸರು ಸುಮಾ",
    "ನೂರ ನಲವತ್ತು ತೊಂಬತ್ತು",
])
def test_clinical_answers_are_never_treated_as_side_talk(text):
    assert not agent.is_side_talk(text), f"{text!r} was suppressed; the worker would be ignored"


def test_naming_sakhi_always_makes_it_our_turn():
    """Ambiguity resolves to answering - silence when asked is the worse bug."""
    assert not agent.is_side_talk("Sakhi, should she sit down")
    assert not agent.is_side_talk("ಸಖಿ, ಕುಳಿತುಕೊಳ್ಳಿ ಅಂದ್ರಾ")


def test_a_question_is_never_side_talk():
    assert not agent.is_side_talk("should she sit down?")
    assert not agent.is_side_talk("do I tell her to take a deep breath?")


def test_side_talk_gate_has_an_escape_hatch(monkeypatch):
    monkeypatch.setenv("VOICE_SIDE_TALK_GATE_ENABLED", "false")
    assert agent.env_flag("VOICE_SIDE_TALK_GATE_ENABLED", True) is False


# --- repeat requests -------------------------------------------------------

@pytest.mark.parametrize("text", [
    "what?", "say that again", "repeat", "repeat please", "pardon",
    "ಇನ್ನೊಮ್ಮೆ ಹೇಳಿ", "ಏನಂದ್ರಿ", "फिर से कहिए", "क्या कहा",
])
def test_repeat_requests_are_recognised(text):
    assert agent.is_repeat_request(text)


@pytest.mark.parametrize("text", [
    "her name is Suma",
    "what is her blood pressure",          # a question FROM the worker, not a repeat
    "she said she has a headache and also some swelling in her feet",
])
def test_substantive_turns_are_not_repeat_requests(text):
    assert not agent.is_repeat_request(text)


def test_repeat_reasks_the_armed_question_verbatim():
    """A paraphrased clinical question is a DIFFERENT question - it invites a
    different answer that gets filed against the same slot."""
    from sakhi_domain import SakhiCallState
    from sakhi_intake import QUESTIONS_BY_ID

    state = SakhiCallState()
    armed = state.intake.arm_next()
    assert state.intake.pending_id == armed.id
    # The handler re-speaks exactly this text; no model call is involved.
    assert QUESTIONS_BY_ID[state.intake.pending_id].text == armed.text


# --- the spelling loop must terminate --------------------------------------

@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "db.json"
    path.write_text(json.dumps({
        "patients": [
            {"patient_id": "M-201", "name": "Suma Gowda", "age": 24,
             "village": "Kamalnagar", "phone": "9876543210",
             "gestational_weeks": 28, "risk_status": "ROUTINE",
             "history": [], "symptoms": {}},
        ],
        "bp_records": [], "teleconsult_logs": [],
    }), encoding="utf-8")
    return MockSixtyDBRepository(path, flush_delay=0.01)


def test_identity_fallback_is_requested_once_then_the_call_moves_on(repo):
    """Never ask about the name a second time over voice. A worker with a
    mother in front of her cannot be held at an identity check - the
    clinical value of the call is the blood pressure, not the database row.

    In the real flow, tools_for() withdraws search_patient the instant
    awaiting_identity_box is set (see test_identity_frontend_fallback.py), so
    the model has no way to call this a second time. This test calls the
    tool method directly, bypassing tools_for, to prove the lookup_attempts
    >= 2 Guest-Patient branch - kept in place defensively - still behaves
    sanely and does not reopen a loop even if invoked again anyway."""
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState()
        ctx = types.SimpleNamespace(userdata=state)

        first = json.loads(await tools.search_patient(ctx, name="Xyzabc Qwerty"))
        assert first["attempt"] == 1
        assert "type the mother's name and age" in first["instruction"]
        assert state.awaiting_identity_box is True

        second = json.loads(await tools.search_patient(ctx, name="Xyzabc Qwerty"))
        assert second["attempt"] == 2
        assert "DO NOT ask for the name or spelling again" in second["instruction"]
        assert second["fallback_name"] == "Xyzabc Qwerty"
        assert "register_mother" in second["instruction"]

        third = json.loads(await tools.search_patient(ctx, name="Xyzabc Qwerty"))
        assert "DO NOT ask" in third["instruction"], "the loop reopened on attempt 3"
    _run(go())


def test_guest_patient_name_is_used_when_no_name_was_heard(repo):
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState()
        state.lookup_attempts = 1          # spelling already asked
        ctx = types.SimpleNamespace(userdata=state)

        result = json.loads(await tools.search_patient(ctx, name=""))
        assert result["fallback_name"] == GUEST_PATIENT_NAME
    _run(go())


def test_a_successful_lookup_still_works(repo):
    """The ceiling must not break the happy path."""
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState()
        ctx = types.SimpleNamespace(userdata=state)
        result = json.loads(await tools.search_patient(ctx, name="Suma Gowda"))
        assert result.get("found") is True
        assert state.lookup_attempts == 0
    _run(go())
