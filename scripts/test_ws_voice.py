"""End-to-end test of Sakhi Direct (sakhi_ws.py) as the app uses it.

    python scripts/test_ws_voice.py [ws://localhost:8787/api/sakhi/ws]

A fake phone: says hello, fetches a line's audio, then answers questions by
streaming REAL spoken Kannada (made by the gateway's own TTS) as 20 ms PCM
frames at real-time pace over light room noise, exactly like the app's mic.
Checks: the answer is heard, silence is detected, a pause inside one answer
stays one answer, and Next mid-answer returns what was said so far.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import time
import wave

import numpy as np
from aiohttp import ClientSession, WSMsgType

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8787/api/sakhi/ws"
HTTP = URL.replace("ws://", "http://").replace("wss://", "https://").rsplit("/ws", 1)[0]
SR = 16000
FRAME = 320  # 20 ms
T0 = time.perf_counter()


def ms() -> int:
    return round((time.perf_counter() - T0) * 1000)


def pcm_of(wav: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav)) as w:
        rate, data = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if rate != SR:
        idx = np.arange(0, len(data), rate / SR)
        data = np.interp(idx, np.arange(len(data)), data).astype(np.int16)
    return data


def noise(seconds: float) -> np.ndarray:
    return (np.random.default_rng(1).normal(0, 60, int(seconds * SR))).astype(np.int16)


async def main() -> int:
    ok = True
    async with ClientSession() as http:
        answers = {}
        for key, text in {"bp": "ಬಿಪಿ ನೂರ ನಲವತ್ತೆಂಟು ಬೈ ತೊಂಬತ್ತಾರು", "a": "ತಲೆನೋವು ಇದೆ", "b": "ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುತ್ತದೆ", "long": "ಎರಡು ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ ಕಾಲು ಊದಿಕೊಂಡಿದೆ"}.items():
            async with http.get(f"{HTTP}/tts", params={"lang": "kn", "text": text}) as r:
                answers[key] = pcm_of(await r.read())
        async with http.ws_connect(URL) as ws:
            inbox: asyncio.Queue = asyncio.Queue()

            async def reader() -> None:
                pending = None
                async for m in ws:
                    if m.type == WSMsgType.TEXT:
                        msg = json.loads(m.data)
                        if msg["t"] == "audio":
                            pending = msg
                            continue
                        await inbox.put(msg)
                    elif m.type == WSMsgType.BINARY and pending:
                        pending["data"] = m.data
                        await inbox.put(pending)
                        pending = None

            asyncio.create_task(reader())

            async def expect(*kinds: str, timeout: float = 20.0) -> dict:
                while True:
                    msg = await asyncio.wait_for(inbox.get(), timeout)
                    if msg["t"] in kinds:
                        return msg

            async def stream(pcm: np.ndarray, stop: asyncio.Event | None = None) -> None:
                for i in range(0, len(pcm) - FRAME + 1, FRAME):
                    if stop and stop.is_set():
                        return
                    await ws.send_bytes(pcm[i : i + FRAME].tobytes())
                    await asyncio.sleep(FRAME / SR)

            await ws.send_str(json.dumps({"t": "hello", "lang": "kn", "lines": ["ತಾಯಿಯ ಇಂದಿನ ಬಿಪಿ ಎಷ್ಟು ದಾಖಲಾಗಿದೆ?"]}))
            r = await expect("ready")
            print(f"[{ms():6d}] 1. ready (provider {r.get('provider')})")

            t = ms()
            await ws.send_str(json.dumps({"t": "tts", "id": 1, "text": "ತಾಯಿಯ ಇಂದಿನ ಬಿಪಿ ಎಷ್ಟು ದಾಖಲಾಗಿದೆ?"}))
            a = await expect("audio", "audioError")
            good = a["t"] == "audio" and a.get("data", b"")[:4] == b"RIFF" and len(a["data"]) == a["bytes"]
            ok &= good
            print(f"[{ms():6d}] 2. line audio {a.get('bytes')} B in {ms() - t} ms (server {a.get('ms')} ms) {'OK' if good else 'FAIL'}")

            async def turn(seq: int, pcm: np.ndarray, hang: int = 800, next_after: float | None = None) -> dict:
                await ws.send_str(json.dumps({"t": "listen", "seq": seq, "hangoverMs": hang, "noSpeechMs": 5000}))
                stop = asyncio.Event()
                sender = asyncio.create_task(stream(np.concatenate([noise(0.6), pcm, noise(6.0)]), stop))
                if next_after is not None:
                    await asyncio.sleep(next_after)
                    await ws.send_str(json.dumps({"t": "next", "seq": seq}))
                res = await expect("heard", "silence", "error", timeout=30)
                spoke_end = 0.6 + len(pcm) / SR
                stop.set()
                await sender
                res["spoke_end"] = spoke_end
                return res

            t = ms()
            r = await turn(3, answers["bp"])
            good = r["t"] == "heard" and "148" in r["text"].replace(" ", "")
            ok &= good
            print(f"[{ms():6d}] 3. heard '{r.get('text')}' stt {r.get('sttMs')} ms, {ms() - t - round(r['spoke_end'] * 1000)} ms after speech ended {'OK' if good else 'FAIL'}")

            r = await turn(4, np.zeros(0, dtype=np.int16))
            good = r["t"] == "silence"
            ok &= good
            print(f"[{ms():6d}] 4. silence detected {'OK' if good else 'FAIL'}")

            gap = np.concatenate([answers["a"], noise(0.45), answers["b"]])
            r = await turn(5, gap, hang=850)
            good = r["t"] == "heard" and len(r["text"].split()) >= 3
            ok &= good
            print(f"[{ms():6d}] 5. one answer across a 0.45 s pause: '{r.get('text')}' {'OK' if good else 'FAIL'}")

            r = await turn(6, answers["long"], next_after=0.6 + len(answers["long"]) / SR * 0.6)
            good = r["t"] == "heard" and len(r["text"]) > 3
            ok &= good
            print(f"[{ms():6d}] 6. Next mid-answer -> '{r.get('text')}' {'OK' if good else 'FAIL'}")

            await ws.send_str(json.dumps({"t": "ping", "at": 1}))
            ok &= (await expect("pong"))["at"] == 1
    print("ALL PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
