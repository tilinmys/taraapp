"""2026-09-25 01:36 call: speech detected at -60 dBFS, Sarvam returned "",
Sakhi said nothing. These pin: quiet audio is amplified, an empty result is
answered out loud, and Sakhi never speaks English on the pilot."""

import array
import asyncio
import json

import numpy as np
import pytest
from livekit import rtc

import agent
import sarvam_wrappers as sw
from sakhi_domain import Language, SakhiCallState
from sakhi_question_lock import QuestionLock
from sakhi_intake import QUESTIONS_BY_ID


def _peak_dbfs(pcm: bytes) -> float:
    a = array.array("h"); a.frombytes(pcm)
    return 20 * np.log10(max(abs(x) for x in a) / 32768)


def test_quiet_utterance_is_lifted_to_speech_level():
    quiet = (np.sin(np.linspace(0, 400, 16000)) * 32768 * 10 ** (-34 / 20)).astype(np.int16).tobytes()
    out, gain = sw.SarvamBatchSTT._normalise(quiet)
    assert 25 <= gain <= 30.01
    assert _peak_dbfs(out) > -6


def test_loud_audio_is_never_attenuated():
    loud = (np.ones(1600) * 30000).astype(np.int16).tobytes()
    out, gain = sw.SarvamBatchSTT._normalise(loud)
    assert gain == 0.0 and out == loud


class _Resp:
    status = 200
    def __init__(self, body): self._b = body
    async def text(self): return self._b
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Session:
    closed = False
    def __init__(self, body): self.body = body
    def post(self, *a, **k): return _Resp(self.body)


def test_empty_transcript_for_real_speech_is_reported(monkeypatch):
    got = []
    monkeypatch.setattr(sw, "STT_EMPTY_SINK", got.append)
    s = sw.SarvamBatchSTT(api_key="k")
    s._session = _Session(json.dumps({"transcript": ""}))
    n = 24000 * 2
    frame = rtc.AudioFrame((np.ones(n) * 20).astype(np.int16).tobytes(), 24000, 1, n)
    asyncio.run(s._recognize_impl([frame]))
    assert got and got[0]["audio_duration_ms"] >= 1900


def test_unheard_speech_is_answered_in_kannada(monkeypatch):
    said = []
    monkeypatch.setattr(agent, "_safe_say", lambda session, text: said.append(text))

    class S:
        agent_state = "listening"
        userdata = SakhiCallState(language=Language.KANNADA)

    handler = agent._make_unheard_speech_handler(S())
    handler({"audio_duration_ms": 2480, "gain_db": 26})
    handler({"audio_duration_ms": 2480, "gain_db": 26})          # rate-limited
    assert len(said) == 1
    assert said[0].startswith("ಕ್ಷಮಿಸಿ") and QUESTIONS_BY_ID["q01"].text in said[0]


def test_unheard_speech_waits_while_sakhi_is_speaking(monkeypatch):
    said = []
    monkeypatch.setattr(agent, "_safe_say", lambda session, text: said.append(text))

    class S:
        agent_state = "speaking"
        userdata = SakhiCallState(language=Language.KANNADA)

    agent._make_unheard_speech_handler(S())({"audio_duration_ms": 1000, "gain_db": 0})
    assert said == []


def test_english_is_never_the_spoken_language(monkeypatch):
    monkeypatch.delenv("SAKHI_ENGLISH_ENABLED", raising=False)
    assert agent.spoken_language(Language.ENGLISH) == Language.KANNADA
    assert agent.spoken_language(Language.HINDI) == Language.HINDI
    monkeypatch.setenv("SAKHI_ENGLISH_ENABLED", "true")
    assert agent.spoken_language(Language.ENGLISH) == Language.ENGLISH


def test_english_sentence_dropped_on_a_kannada_call_but_latin_name_kept():
    lock = QuestionLock(QUESTIONS_BY_ID["q02"].text, "FAST", ())
    out = "".join(lock.feed("Okay, noted the details. ಸರಿ, Priya Sharma, 28 ವರ್ಷ ದಾಖಲಾಗಿದೆ. "))
    out += "".join(lock.finish(final=True))
    assert "Okay" not in out
    assert "Priya Sharma" in out
