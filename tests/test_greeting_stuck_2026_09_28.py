"""Greeting cut off mid-sentence + false "didn't hear" (2026-09-28 call).

Timeline: greeting started 12.51 s, a sound at 15.66 s cut it off (echo of
Sakhi's own voice), 0.6 s of word-less "speech", then Sakhi apologised for
not hearing anything - heard by the worker as the voice getting stuck.
"""

import inspect
import re

import agent
from agent import Language, SakhiCallState


def test_greeting_cannot_be_interrupted():
    src = inspect.getsource(agent.entrypoint)
    start = src.index("if cached_greeting_frames:")
    block = src[start:src.index("ensure_greeting_cache", start)]
    assert "allow_interruptions=True" not in block
    assert len(re.findall(r"allow_interruptions=False\s*\)", block)) == 2   # both say() calls


class _Session:
    def __init__(self, state="listening"):
        self.agent_state = state
        self.userdata = SakhiCallState(language=Language.KANNADA)
        self.said = []


def test_overlapping_sound_does_not_trigger_didnt_hear(monkeypatch):
    session = _Session()
    monkeypatch.setattr(agent, "_safe_say", lambda s, t: s.said.append(t))
    handler = agent._make_unheard_speech_handler(session, suppressed=lambda: True)
    handler({"audio_duration_ms": 600, "gain_db": 0, "reason": "no_transcript_after_speech"})
    assert session.said == []


def test_real_unheard_speech_still_prompts(monkeypatch):
    session = _Session()
    monkeypatch.setattr(agent, "_safe_say", lambda s, t: s.said.append(t))
    handler = agent._make_unheard_speech_handler(session, suppressed=lambda: False)
    handler({"audio_duration_ms": 2400, "gain_db": 12, "reason": "empty"})
    assert len(session.said) == 1


def test_watchdog_records_overlap_with_sakhi_speech():
    src = inspect.getsource(agent.entrypoint)
    assert '_heard["overlapped"] = getattr(session, "agent_state", None) == "speaking"' in src
    assert 'and not _heard["overlapped"]' in src
