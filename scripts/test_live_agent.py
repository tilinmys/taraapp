"""End-to-end test of Live Sakhi's voice agent, exactly as the app uses it.

    python scripts/test_live_agent.py            (gateway + agent running)

A real LiveKit participant (like the app's WebView) gets a session from the
gateway, joins the room, waits for the agent, and then plays the ENGINE's
part: one command per turn. The "ASHA" is real spoken Kannada (made by the
gateway's own TTS) streamed into the room in real time over room noise.

Checks, with timings:
  1. hello -> ready
  2. say Q + listen, ASHA answers  -> said, speech, heard (correct digits)
  3. say Q + listen, ASHA is silent -> silence after no-speech
  4. say Q + listen, ASHA pauses mid-answer (shorter than the hangover)
     -> ONE answer, both halves
  5. Next while she is still talking -> heard with what was said so far
  6. a stale reply never matches a newer seq (checked on every message)
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import random
import struct
import sys
import time
import urllib.parse
import urllib.request
import wave

from livekit import rtc

GATEWAY = os.getenv("SAKHI_GATEWAY_INTERNAL", "http://localhost:8787/api/sakhi")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dev_token import ensure_dev_token  # noqa: E402

# The gateway refuses requests without its bearer token (the dev scripts' token).
AUTH = {"Authorization": f"Bearer {ensure_dev_token()}"}
TOPIC = "sakhi.app"
SR = 16000


def http_json(url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json", **AUTH}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def tts_pcm(text: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(f"{GATEWAY}/tts?lang=kn&text={urllib.parse.quote(text)}", headers=AUTH), timeout=30) as r:
        with wave.open(io.BytesIO(r.read()), "rb") as w:
            assert w.getframerate() == SR and w.getnchannels() == 1
            return w.readframes(w.getnframes())


def noise(ms: int, amp: int = 120) -> bytes:
    n = SR * ms // 1000
    return struct.pack(f"<{n}h", *(random.randint(-amp, amp) for _ in range(n)))


def mix_noise(pcm: bytes, amp: int = 120) -> bytes:
    n = len(pcm) // 2
    samples = struct.unpack(f"<{n}h", pcm)
    return struct.pack(f"<{n}h", *(max(-32768, min(32767, s + random.randint(-amp, amp))) for s in samples))


class Client:
    def __init__(self) -> None:
        self.room = rtc.Room()
        self.source = rtc.AudioSource(SR, 1, queue_size_ms=200)
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.t0 = time.perf_counter()
        self.mic_task: asyncio.Task | None = None
        self.playing: bytes = b""

    def ms(self) -> int:
        return round((time.perf_counter() - self.t0) * 1000)

    async def connect(self) -> None:
        s = http_json(f"{GATEWAY}/live/session", {"lang": "kn", "mother": {"id": "e2e", "name": "Test Mother", "months": 7}})
        print(f"[{self.ms():6d}] session {s['room']} (server {s['ms']} ms)")

        @self.room.on("data_received")
        def _on_data(p: rtc.DataPacket) -> None:
            if p.topic == TOPIC:
                self.inbox.put_nowait((self.ms(), json.loads(p.data.decode())))

        joined = asyncio.Event()

        @self.room.on("participant_connected")
        def _on_join(p: rtc.RemoteParticipant) -> None:
            joined.set()

        await self.room.connect(s["url"], s["token"])
        print(f"[{self.ms():6d}] connected")
        track = rtc.LocalAudioTrack.create_audio_track("asha-mic", self.source)
        await self.room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        self.mic_task = asyncio.create_task(self._mic())
        if not self.room.remote_participants:
            await asyncio.wait_for(joined.wait(), 25)
        print(f"[{self.ms():6d}] agent in the room")

    async def _mic(self) -> None:
        """A continuous 'microphone': room noise, or speech when queued."""
        while True:
            chunk = SR // 50 * 2
            if self.playing:
                data, self.playing = self.playing[:chunk], self.playing[chunk:]
                data = data.ljust(chunk, b"\0")
            else:
                data = noise(20)
            await self.source.capture_frame(rtc.AudioFrame(data, SR, 1, chunk // 2))

    def speak(self, pcm: bytes) -> float:
        self.playing += mix_noise(pcm)
        return len(pcm) / 2 / SR

    async def send(self, msg: dict) -> None:
        await self.room.local_participant.publish_data(json.dumps(msg), reliable=True, topic=TOPIC)

    async def wait(self, *types: str, seq: int | None = None, timeout: float = 30) -> tuple[int, dict]:
        end = time.perf_counter() + timeout
        while True:
            left = end - time.perf_counter()
            if left <= 0:
                raise TimeoutError(f"no {types} (seq {seq})")
            at, msg = await asyncio.wait_for(self.inbox.get(), left)
            if seq is not None and "seq" in msg and msg["seq"] != seq:
                print(f"[{at:6d}]   (stale {msg['t']} seq {msg['seq']} ignored)")
                continue
            if msg["t"] in types:
                return at, msg
            print(f"[{at:6d}]   {msg['t']}")


async def main() -> int:
    q1, q2, q3 = "ತಾಯಿಯ ಇಂದಿನ ಬಿಪಿ ಎಷ್ಟು ದಾಖಲಾಗಿದೆ?", "ತಾಯಿಯ ತೂಕ ಎಷ್ಟು ಕೆಜಿ ಇದೆ?", "ತೀವ್ರ ತಲೆನೋವು ಇದೆಯಾ?"
    bp = tts_pcm("ಬಿಪಿ 148 ಬೈ 96 ಇದೆ")
    part_a, part_b = tts_pcm("ತಲೆನೋವು ಇದೆ"), tts_pcm("ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುತ್ತದೆ")
    long_answer = tts_pcm("ಎರಡು ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವು ಇದೆ, ಕಾಲು ಊದಿಕೊಂಡಿದೆ, ರಾತ್ರಿ ನಿದ್ದೆ ಬರುತ್ತಿಲ್ಲ")
    c = Client()
    await c.connect()
    failures = 0

    # Like the app: hello until ready (the agent may join before it listens).
    for _ in range(15):
        await c.send({"t": "hello", "lang": "kn", "lines": [q1, q2, q3]})
        try:
            at, _ = await c.wait("ready", timeout=1.0)
            break
        except (TimeoutError, asyncio.TimeoutError):
            continue
    else:
        print("1. agent never ready: FAIL")
        return 1
    print(f"[{at:6d}] 1. ready")

    # 2. A normal answer with numbers.
    await c.send({"t": "say", "seq": 1, "text": q1, "listen": {"hangoverMs": 800, "noSpeechMs": 7000}})
    at, _ = await c.wait("said", seq=1)
    print(f"[{at:6d}] 2. said Q (the agent is listening)")
    await asyncio.sleep(0.6)
    dur = c.speak(bp)
    spoke_end = c.ms() + dur * 1000
    at, msg = await c.wait("heard", seq=1)
    ok = "148" in msg["text"] and "96" in msg["text"]
    failures += not ok
    print(f"[{at:6d}]    heard '{msg['text']}' stt {msg['ms']} ms, {round(at - spoke_end)} ms after she stopped speaking {'OK' if ok else 'FAIL'}")

    # 3. Silence.
    await c.send({"t": "say", "seq": 2, "text": q2, "listen": {"hangoverMs": 800, "noSpeechMs": 4000}})
    await c.wait("said", seq=2)
    t_listen = c.ms()
    at, msg = await c.wait("silence", "heard", seq=2)
    ok = msg["t"] == "silence"
    failures += not ok
    print(f"[{at:6d}] 3. {msg['t']} after {at - t_listen} ms of room noise {'OK' if ok else 'FAIL'}")

    # 4. A pause inside one answer (shorter than the hangover) -> one answer.
    await c.send({"t": "say", "seq": 3, "text": q3, "listen": {"hangoverMs": 900, "noSpeechMs": 7000}})
    await c.wait("said", seq=3)
    await asyncio.sleep(0.5)
    c.speak(part_a + b"\0\0" * int(SR * 0.45) + part_b)
    at, msg = await c.wait("heard", seq=3)
    # "ತಲೆನೋವು ಇದೆ" or the joined "ತಲೆನೋವಿದೆ": both are right.
    ok = "ತಲೆನೋವ" in msg["text"] and ("ಕಣ್ಣು" in msg["text"] or "ಮಂಜ" in msg["text"])
    failures += not ok
    print(f"[{at:6d}] 4. one answer across a 0.45 s pause: '{msg['text']}' {'OK' if ok else 'FAIL'}")

    # 5. Next while she is still talking.
    await c.send({"t": "say", "seq": 4, "text": q3, "listen": {"hangoverMs": 900, "noSpeechMs": 7000}})
    await c.wait("said", seq=4)
    await asyncio.sleep(0.5)
    c.speak(long_answer)
    await c.wait("speech", seq=4)
    await asyncio.sleep(2.0)
    await c.send({"t": "next", "seq": 4})
    t_next = c.ms()
    at, msg = await c.wait("heard", "silence", seq=4)
    ok = msg["t"] == "heard" and len(msg.get("text", "")) > 3
    failures += not ok
    print(f"[{at:6d}] 5. Next mid-answer -> {msg['t']} '{msg.get('text', '')}' {at - t_next} ms after Next {'OK' if ok else 'FAIL'}")
    c.playing = b""

    await c.send({"t": "stop"})
    await c.room.disconnect()
    print("ALL PASSED" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
