"""The call must not hang up on the greeting.

Reported as "the frontend is getting stopped when talking": the room was
deleted the instant Sakhi finished her greeting. _FAREWELL_RE matched
"ನಮಸ್ಕಾರ" / "नमस्ते" / "namaste", which in Kannada and Hindi mean BOTH hello
and goodbye - and every greeting opens with that word. It also matched a bare
"thank you", which Sakhi says routinely mid-call.
"""

import pytest

import agent
from sakhi_domain import Language


# --- the greeting must never read as a goodbye ----------------------------

@pytest.mark.parametrize("language", [Language.KANNADA, Language.HINDI, Language.ENGLISH])
def test_greeting_is_not_detected_as_a_farewell(language):
    greeting = agent.greeting_for(language)
    assert not agent._FAREWELL_RE.search(greeting), (
        f"{language.value} greeting {greeting!r} matches the farewell pattern; "
        "the call will hang up before the ASHA worker can speak"
    )


def test_greeting_constant_is_not_a_farewell():
    assert not agent._FAREWELL_RE.search(agent.GREETING_TEXT)


# --- ordinary courteous turns must not end the call ------------------------

@pytest.mark.parametrize("line", [
    "ಧನ್ಯವಾದಗಳು ಅಕ್ಕಾರ. ಈಗ ರಕ್ತದೊತ್ತಡ ಹೇಳಿ.",      # "thank you, now tell me the BP"
    "धन्यवाद दीदी। अब रक्तचाप बताइए।",
    "Thank you Didi. I have noted the reading.",
    "Thank you, that is recorded.",
])
def test_mid_call_courtesy_does_not_end_the_call(line):
    assert not agent._FAREWELL_RE.search(line), (
        f"{line!r} would hang up the call; thanking the worker is not a goodbye"
    )


# --- the real scripted goodbye must still be detected ----------------------

@pytest.mark.parametrize("language", [Language.KANNADA, Language.HINDI, Language.ENGLISH])
def test_the_scripted_goodbye_is_still_detected(language):
    goodbye = agent.goodbye_for(language)
    assert agent._FAREWELL_RE.search(goodbye), (
        f"{language.value} goodbye {goodbye!r} no longer matches; the line "
        "would stay open until the silence timeout"
    )


# --- the independent guard -------------------------------------------------

class _FakeAgent:
    """Exercises the guard without constructing a full SakhiAgent."""

    _farewell_hangup_scheduled = False
    _user_has_spoken = False
    session = None

    _maybe_schedule_farewell_hangup = agent.SakhiAgent._maybe_schedule_farewell_hangup


def test_farewell_before_any_user_turn_is_suppressed(monkeypatch):
    """Belt and braces: even a matching phrase cannot end a call that has not
    started. Guards against a future loosening of the regex."""
    called = []
    monkeypatch.setattr(agent, "schedule_hangup_after_playout", lambda s: called.append(s))

    fake = _FakeAgent()
    fake._maybe_schedule_farewell_hangup(agent.goodbye_for(Language.KANNADA))

    assert called == [], "hung up before the ASHA worker had spoken"
    assert not fake._farewell_hangup_scheduled


def test_farewell_after_a_user_turn_still_hangs_up(monkeypatch):
    called = []
    monkeypatch.setattr(agent, "schedule_hangup_after_playout", lambda s: called.append(s))

    fake = _FakeAgent()
    fake._user_has_spoken = True
    fake._maybe_schedule_farewell_hangup(agent.goodbye_for(Language.KANNADA))

    assert len(called) == 1, "the scripted goodbye no longer ends the call"
    assert fake._farewell_hangup_scheduled
