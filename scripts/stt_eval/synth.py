"""Render the STT test set to 16 kHz mono PCM16 WAV (what the app records).

    python scripts/stt_eval/synth.py            # -> scripts/stt_eval/audio/<id>[_fast|_noisy].wav

Voices: Microsoft edge-tts Indian voices, alternating female / male per case.
Variants for the STRESS subset: _fast (+40 % speaking rate) and _noisy (pink
noise mixed in at 12 dB SNR, seeded: the same file every run).
Only the synthetic test sentences leave this PC (to the edge-tts service).
"""

from __future__ import annotations

import asyncio
import io
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from testset import CASES, STRESS  # noqa: E402

OUT = HERE / "audio"
VOICES = {"kn": ["kn-IN-SapnaNeural", "kn-IN-GaganNeural"], "hi": ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"], "en": ["en-IN-NeerjaNeural", "en-IN-PrabhatNeural"]}
SNR_DB = 12.0


async def tts_mp3(text: str, voice: str, rate: str) -> bytes:
    import edge_tts

    out = io.BytesIO()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk["type"] == "audio":
            out.write(chunk["data"])
    return out.getvalue()


def to_pcm16k(mp3: bytes) -> np.ndarray:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"], input=mp3, capture_output=True, check=True)
    return np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32)


def add_noise(pcm: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(pcm.size + 64)
    pink = np.convolve(white, np.ones(64) / 64, mode="valid")[: pcm.size]  # low-passed: a room / fan hum
    pink += 0.3 * white[: pcm.size]
    sig = np.sqrt(np.mean(pcm**2)) or 1.0
    noise_rms = sig / (10 ** (SNR_DB / 20))
    return pcm + pink / (np.sqrt(np.mean(pink**2)) or 1.0) * noise_rms


def write_wav(path: Path, pcm: np.ndarray) -> None:
    pcm = np.clip(pcm, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    stress = set(STRESS)
    done = 0
    for i, c in enumerate(CASES):
        voice = VOICES[c["lang"]][i % 2]
        jobs = [("", "+0%")] + ([("_fast", "+40%")] if c["id"] in stress else [])
        for suffix, rate in jobs:
            path = OUT / f"{c['id']}{suffix}.wav"
            if path.exists():
                continue
            for attempt in range(3):
                try:
                    pcm = to_pcm16k(await tts_mp3(c["text"], voice, rate))
                    break
                except Exception as exc:  # network hiccup: retry
                    if attempt == 2:
                        raise
                    print("retry", c["id"], exc)
                    await asyncio.sleep(1.5)
            write_wav(path, pcm)
            if suffix == "" and c["id"] in stress:
                write_wav(OUT / f"{c['id']}_noisy.wav", add_noise(pcm, seed=i))
            done += 1
    print(f"{len(CASES)} cases, {done} files rendered now, in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
