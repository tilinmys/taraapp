"""End-to-end synthetic call against a deployed Sakhi worker.

Joins a real LiveKit room exactly as the browser console does (a normal
participant with a microphone track), has the agent dispatched to it, waits
for the greeting, then "speaks" a WAV file as its microphone - so the audio
goes through everything a real call does: WebRTC/Opus, the worker's BVC noise
cancellation, Silero VAD, the turn detector, Sarvam STT, on_user_turn_completed
and the intake extractor.

Prints the room name; read that room's pipeline events on the server to see
what each stage did (see docs/STT_FRAGMENTATION_INCIDENT.md for the command).

    python scripts/e2e_call_probe.py path/to/answer.wav [--condition speaker]

Synthetic audio only. Note: the worker saves this as a real call (a test
mother and call report appear in the database).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from livekit import api, rtc  # noqa: E402

OUT_RATE = 48000                 # what a browser publishes


def load(path: str, condition: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if condition == "pauses":
        # The answer spoken in parts, as on the 2026-09-25 call: name ...
        # 1.8 s pause ... age. Split at the quietest point near the middle.
        mid = len(x) // 2
        win = int(rate * 0.4)
        env = np.convolve(np.abs(x), np.ones(int(rate * 0.05)), mode="same")
        cut = mid - win + int(np.argmin(env[mid - win: mid + win]))
        x = np.concatenate([x[:cut], np.zeros(int(rate * 1.8), dtype=np.float32), x[cut:]])
    elif condition != "clean":
        import stt_accuracy_probe as p     # same degradations as the raw STT probe
        p.RATE = rate
        x = p.condition(condition, x, np.random.default_rng(0))
    t_in = np.arange(len(x)) / rate
    t_out = np.arange(int(len(x) * OUT_RATE / rate)) / OUT_RATE
    return np.interp(t_out, t_in, x).astype(np.float32)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--condition", default="clean")
    ap.add_argument("--greeting-wait", type=float, default=9.0)
    ap.add_argument("--after", type=float, default=10.0)
    args = ap.parse_args()

    url, key, secret = os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
    agent_name = os.getenv("LIVEKIT_AGENT_NAME", "sakhi-maternal-care")
    room_name = f"sakhi-room-probe-{uuid.uuid4().hex[:10]}"
    audio = load(args.wav, args.condition)

    lk = api.LiveKitAPI(url.replace("wss://", "https://"), key, secret)
    await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
        agent_name=agent_name, room=room_name, metadata=json.dumps({"language": "kannada"})))
    token = (api.AccessToken(key, secret).with_identity(f"probe-caller-{uuid.uuid4().hex[:6]}")
             .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt())

    room = rtc.Room()
    agent_joined = asyncio.Event()
    room.on("participant_connected", lambda p: agent_joined.set())
    await room.connect(url, token)
    if room.remote_participants:          # dispatched agent can be in the room first
        agent_joined.set()
    print(json.dumps({"room": room_name, "host": url.split("//")[-1].split(".")[0],
                      "condition": args.condition}))

    source = rtc.AudioSource(OUT_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    step = OUT_RATE // 100

    async def push(samples: np.ndarray):
        pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        for i in range(0, len(pcm) - step + 1, step):
            await source.capture_frame(rtc.AudioFrame(pcm[i:i + step].tobytes(), OUT_RATE, 1, step))

    try:
        await asyncio.wait_for(agent_joined.wait(), timeout=20)
        await push(np.zeros(int(OUT_RATE * args.greeting_wait), dtype=np.float32))   # let the greeting play
        await push(audio)                                                            # the answer
        # Stay connected while Sakhi replies. Pushing silent frames here could
        # return early (the caller then left mid-reply); keep a short tail of
        # real silence for the VAD, then just wait with the track published.
        await push(np.zeros(int(OUT_RATE * 1.0), dtype=np.float32))
        await asyncio.sleep(args.after)
    finally:
        await room.disconnect()
        await lk.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
