"""STT segmentation by our own VAD (2026-09-25 call: Sarvam's server VAD
dropped two of three ASHA speech bursts). See docs/STT_FRAGMENTATION_INCIDENT.md."""

import asyncio
import json

import numpy as np
import pytest
from livekit import rtc
from livekit.agents import stt

import agent
import sarvam_wrappers as sw
from sakhi_domain import Language, SakhiCallState
from sakhi_intake import QUESTIONS_BY_ID, armed_question_text


def _frame(seconds=1.0, rate=24000, value=8000):
    n = int(seconds * rate)
    return rtc.AudioFrame((np.full(n, value, dtype=np.int16)).tobytes(), rate, 1, n)


def test_utterance_is_resampled_to_16k_mono_wav():
    wav, samples = sw.SarvamBatchSTT._to_wav16k([_frame(0.5), _frame(0.5)])
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert abs(samples - 16000) < 400          # 1 s at 16 kHz


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body
    async def text(self):
        return self._body
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False


class _Session:
    closed = False
    def __init__(self, resp):
        self.resp, self.calls = resp, []
    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, data, headers))
        return self.resp


def _batch(resp):
    s = sw.SarvamBatchSTT(api_key="k", language_code="kn-IN", model="saaras:v3", mode="transcribe")
    s._session = _Session(resp)
    return s


def test_one_request_per_utterance_returns_a_final(monkeypatch):
    seen = []
    monkeypatch.setattr(sw, "STT_DEBUG_SINK", seen.append)
    s = _batch(_Resp(200, json.dumps({"transcript": "ತಾಯಿಯ ಹೆಸರು ಪ್ರಿಯಾ", "request_id": "r1"})))
    ev = asyncio.run(s._recognize_impl([_frame(1.0)], language="en"))
    assert ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT
    assert ev.alternatives[0].text == "ತಾಯಿಯ ಹೆಸರು ಪ್ರಿಯಾ"
    assert ev.alternatives[0].language == "kn-IN"          # "en" hint is not a Sarvam code
    assert len(s._session.calls) == 1 and s._session.calls[0][0].endswith("/speech-to-text")
    assert seen and seen[0]["segmentation"] == "livekit_vad" and "transcript" not in seen[0]


@pytest.mark.parametrize("status, retryable", [(500, True), (429, True), (400, False)])
def test_provider_errors_surface_so_the_fallback_can_take_over(status, retryable):
    s = _batch(_Resp(status, "{}"))
    with pytest.raises(sw.APIStatusError) as err:
        asyncio.run(s._recognize_impl([_frame(0.3)]))
    assert err.value.retryable is retryable


def test_build_stt_segments_with_our_vad(monkeypatch):
    monkeypatch.setenv("STT_PRIMARY", "sarvam")
    monkeypatch.setenv("SARVAM_API_KEY", "k")
    monkeypatch.setenv("VOICE_STT_FALLBACK_ENABLED", "false")
    monkeypatch.delenv("SARVAM_STT_SEGMENTATION", raising=False)
    built = agent.build_stt(Language.KANNADA, segment_vad=agent._build_stt_segmentation_vad())
    assert isinstance(built, stt.StreamAdapter)
    assert isinstance(built.wrapped_stt if hasattr(built, "wrapped_stt") else built._stt, sw.SarvamBatchSTT)


def test_provider_segmentation_is_still_available(monkeypatch):
    monkeypatch.setenv("STT_PRIMARY", "sarvam")
    monkeypatch.setenv("SARVAM_API_KEY", "k")
    monkeypatch.setenv("VOICE_STT_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("SARVAM_STT_SEGMENTATION", "provider")
    assert isinstance(agent.build_stt(Language.KANNADA, segment_vad=object()), sw.SarvamSTT)


# --- partial q01 answers ------------------------------------------------------

def test_age_heard_but_not_name_asks_only_for_the_name():
    state = SakhiCallState(language=Language.KANNADA)
    state.identify(age=28)
    text = armed_question_text(QUESTIONS_BY_ID["q01"], state, state.language)
    assert "ಹೆಸರು" in text and "ವಯಸ್ಸು" not in text


def test_name_heard_but_not_age_asks_only_for_the_age():
    state = SakhiCallState(language=Language.HINDI)
    state.identify(name="सीमा")
    assert "उम्र" in armed_question_text(QUESTIONS_BY_ID["q01"], state, state.language)


def test_nothing_known_asks_the_full_question():
    state = SakhiCallState(language=Language.KANNADA)
    assert armed_question_text(QUESTIONS_BY_ID["q01"], state, state.language) == QUESTIONS_BY_ID["q01"].text
