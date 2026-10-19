"""Raw STT accuracy probe for the q01 answer (no LLM, no LiveKit room).

Question it answers: given a known Kannada sentence, does Sakhi's own
SarvamSTT wrapper return a transcript the intake extractor can use?

    "ತಾಯಿಯ ಹೆಸರು ಪ್ರಿಯಾ ಶರ್ಮಾ, ವಯಸ್ಸು ಇಪ್ಪತ್ತೆಂಟು ವರ್ಷ."

The sentence is synthesised once with Sarvam TTS (a voice other than
Sakhi's), then degraded into four listening conditions and streamed through
SarvamSTT exactly the way the worker does: 24 kHz mono 10 ms frames, in real
time, with leading/trailing silence and a flush at the end.

A run PASSES only when sakhi_domain.detect_patient_demographics() on the
canonicalised transcript yields age 28 AND the transcript contains the name
(ಪ್ರಿಯಾ / Priya). That is the same extraction q01 depends on - nothing here
is special-cased for this sentence.

Usage:
    python scripts/stt_accuracy_probe.py                 # 10 runs x 4 conditions
    python scripts/stt_accuracy_probe.py --runs 3 --conditions clean,speaker
    SARVAM_STT_HIGH_VAD_SENSITIVITY=false python scripts/stt_accuracy_probe.py

Needs SARVAM_API_KEY (.env). Synthetic audio only - no patient data.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

ROOT = Path(os.getenv("STT_PROBE_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from livekit import rtc  # noqa: E402
from livekit.agents import stt as lk_stt  # noqa: E402

from clinical_terms import canonicalise  # noqa: E402
from sakhi_domain import detect_patient_demographics  # noqa: E402
from sarvam_wrappers import SarvamSTT  # noqa: E402

SENTENCE = "ತಾಯಿಯ ಹೆಸರು ಪ್ರಿಯಾ ಶರ್ಮಾ, ವಯಸ್ಸು ಇಪ್ಪತ್ತೆಂಟು ವರ್ಷ."
RATE = 24000                       # what the worker's resampler receives
CACHE = Path(os.getenv("STT_PROBE_CACHE") or ROOT / "logs" / "stt_probe")


# --- fixture ---------------------------------------------------------------

def synthesise(text: str, speaker: str, pace: float) -> np.ndarray:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"fixture-{speaker}-{pace}.wav"
    if not path.exists():
        body = json.dumps({
            "text": text, "target_language_code": "kn-IN", "speaker": speaker,
            "model": "bulbul:v3", "pace": pace, "speech_sample_rate": RATE,
        }).encode()
        req = urllib.request.Request(
            "https://api.sarvam.ai/text-to-speech", data=body, method="POST",
            headers={"api-subscription-key": os.environ["SARVAM_API_KEY"],
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            audio = base64.b64decode(json.loads(resp.read())["audios"][0])
        path.write_bytes(audio)
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1, (w.getframerate(), w.getnchannels())
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


# --- listening conditions --------------------------------------------------

def _noise(n: int, rng) -> np.ndarray:
    white = rng.standard_normal(n)
    pink = np.cumsum(white)                      # brown-ish room rumble
    pink -= np.convolve(pink, np.ones(2400) / 2400, mode="same")
    return (0.6 * white / np.std(white) + 0.4 * pink / (np.std(pink) + 1e-9))


def _at_snr(signal, noise, snr_db):
    ps, pn = np.mean(signal ** 2), np.mean(noise ** 2)
    return signal + noise * np.sqrt(ps / (pn * 10 ** (snr_db / 10)))


def _bandlimit(x, lo, hi):
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / RATE)
    spec[(freqs < lo) | (freqs > hi)] = 0
    return np.fft.irfft(spec, len(x))


def _reverb(x, rt60=0.35, rng=None):
    n = int(RATE * rt60)
    ir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)
    ir[0] = 1.0
    return np.convolve(x, ir / np.sum(np.abs(ir)) * 6, mode="full")[: len(x)]


def condition(name: str, clean: np.ndarray, rng) -> np.ndarray:
    if name == "clean":
        out = clean
    elif name == "quiet":                        # soft voice, far from the tablet
        out = clean * 10 ** (-24 / 20)
    elif name == "faint":                        # the 2026-09-25 01:36 call: ~-56 dBFS RMS
        out = _at_snr(clean * 10 ** (-36 / 20), _noise(len(clean), rng), 20)
    elif name == "room_noise":                   # fan, road, children: SNR 10 dB
        out = _at_snr(clean, _noise(len(clean), rng), 10)
    elif name == "speaker":                      # phone speaker -> tablet mic
        out = _bandlimit(clean, 300, 3800)
        out = _reverb(out, rng=rng)
        out = _at_snr(out * 0.5, _noise(len(out), rng), 15)
    else:
        raise ValueError(name)
    return np.clip(out, -1, 1)


# --- one run through the production wrapper ---------------------------------

def frames(audio: np.ndarray):
    pcm = (np.concatenate([np.zeros(RATE // 2), audio, np.zeros(RATE * 2)]) * 32767).astype(np.int16)
    step = RATE // 100
    for i in range(0, len(pcm) - step, step):
        yield rtc.AudioFrame(pcm[i:i + step].tobytes(), RATE, 1, step)


async def transcribe(audio: np.ndarray) -> tuple[list[str], float]:
    s = SarvamSTT(api_key=os.environ["SARVAM_API_KEY"], language_code="kn-IN",
                  model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
                  mode=os.getenv("SARVAM_STT_MODE", "codemix"))
    stream = s.stream()
    finals: list[str] = []
    started = time.perf_counter()

    async def feed():
        for f in frames(audio):
            stream.push_frame(f)
            await asyncio.sleep(0.01)
        stream.flush()
        await asyncio.sleep(3)
        stream.end_input()

    async def read():
        async for ev in stream:
            if ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives:
                finals.append(ev.alternatives[0].text)

    feeder = asyncio.create_task(feed())
    try:
        await asyncio.wait_for(read(), timeout=len(audio) / RATE + 12)
    except asyncio.TimeoutError:
        pass
    finally:
        feeder.cancel()
        await stream.aclose()
    return finals, time.perf_counter() - started


def score(finals: list[str]) -> tuple[bool, dict]:
    joined = " ".join(finals)
    name, age = detect_patient_demographics(canonicalise(joined))
    has_name = any(k in joined.lower() for k in ("ಪ್ರಿಯಾ", "ಪ್ರಿಯ", "priya"))
    return (age == 28 and has_name), {"name": name, "age": age, "has_name": has_name}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--conditions", default="clean,quiet,room_noise,speaker")
    ap.add_argument("--speaker", default="kavya")
    ap.add_argument("--pace", type=float, default=1.1)   # an ASHA worker speaks briskly
    ap.add_argument("--json")
    args = ap.parse_args()

    clean = synthesise(SENTENCE, args.speaker, args.pace)
    cfg = {k: os.getenv(k) for k in ("SARVAM_STT_MODEL", "SARVAM_STT_MODE",
                                      "SARVAM_STT_HIGH_VAD_SENSITIVITY",
                                      "SARVAM_STT_FLUSH_SIGNAL_ENABLED")}
    print(f"fixture {len(clean) / RATE:.1f}s  config {cfg}")
    report = {}
    for cond in args.conditions.split(","):
        passes, rows = 0, []
        for i in range(args.runs):
            audio = condition(cond, clean, np.random.default_rng(i))
            finals, took = await transcribe(audio)
            ok, detail = score(finals)
            passes += ok
            rows.append({"finals": finals, "ok": ok, **detail})
            print(f"  {cond:<11} run {i + 1:>2}  {'PASS' if ok else 'FAIL'}  "
                  f"{len(finals)} final(s) {finals}")
        report[cond] = {"passed": passes, "runs": args.runs, "rows": rows}
        print(f"== {cond}: {passes}/{args.runs}")
    print("SUMMARY " + "  ".join(f"{c}={r['passed']}/{r['runs']}" for c, r in report.items()))
    if args.json:
        Path(args.json).write_text(json.dumps({"config": cfg, "report": report},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
