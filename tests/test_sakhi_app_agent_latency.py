"""The live app agent's latency work: recorded lines, speculative STT."""

import asyncio
import json

import pytest

import sakhi_app_agent as agent


def test_every_recorded_line_loads_and_needs_no_tts():
    manifest = json.loads((agent.RECORDINGS_DIR / "manifest.json").read_text(encoding="utf-8"))
    for lang in ("kn", "hi"):
        rec = agent.load_recordings(lang)
        assert len(rec) == len(manifest["lines"][lang]) >= 55
        pcm, rate = next(iter(rec.values()))
        assert rate == 16000 and len(pcm) > 16000  # real audio, > 0.5 s

    class NoHttp:
        def get(self, *a, **k):
            raise AssertionError("a recorded line must not call TTS")

    voice = agent.Voice(NoHttp(), "kn", agent.load_recordings("kn"))
    line = next(iter(manifest["lines"]["kn"].values()))["text"]
    assert asyncio.run(voice.pcm("  " + line + " ")) == voice.recorded[agent._key(line)]
    assert voice.has(line)


class FakeEars:
    def __init__(self):
        self.calls = 0

    async def transcribe(self, frames):
        self.calls += 1
        await asyncio.sleep(0.05)
        return f"heard {len(frames)}", 50


def _executor():
    ex = agent.Executor.__new__(agent.Executor)
    ex.ears = FakeEars()
    ex.seq = 7
    ex.state = "hearing"
    ex.segments = [object()] * 40
    ex.speech_ms = 900.0
    ex.speech_end = 30
    ex.end_timer = None
    ex.answer_done = None
    ex.spec = None
    sent = []

    async def send(msg):
        sent.append(msg)

    async def state(s):
        ex.state = s

    ex._send, ex._state, ex.sent = send, state, sent
    return ex


@pytest.mark.asyncio
async def test_speculative_stt_is_used_when_she_says_nothing_more():
    ex = _executor()
    ex._speculate()  # end of speech
    await asyncio.sleep(0.06)  # the hangover passes; the guess is done
    await ex._finish_answer()
    assert ex.ears.calls == 1  # no second transcription
    assert ex.sent[-1]["t"] == "heard" and ex.sent[-1]["text"] == "heard 40"


@pytest.mark.asyncio
async def test_speculative_stt_is_dropped_when_she_goes_on():
    ex = _executor()
    ex._speculate()
    ex._drop_spec()  # START_OF_SPEECH: she continued
    ex.segments = [object()] * 60
    ex.speech_end = 55
    await ex._finish_answer()
    # the stale guess (40 frames) was cancelled; the answer is the whole 60
    assert ex.sent[-1]["text"] == "heard 60"
