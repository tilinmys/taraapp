"""One-off live provider test, 2026-09-02.

Not a pytest file - a throwaway diagnostic script run manually against real
credentials, in the spirit of every other *_benchmark.json this repo already
has (Vakyam, Sarvam/Gnani TTS). Answers three questions with real API calls
instead of stale comments:

  1. TTS: is Vakyam/Ananya still the fastest, and does it still work? (single
     provider chosen for TTS - no fallback per the pilot's 2026-09-02 direction)
  2. STT: Deepgram nova-3 vs Sarvam saarika:v2.5 on the SAME synthesized
     Kannada clinical phrase - latency AND transcript accuracy. Deepgram is
     the current primary but this repo's own .env carries an unresolved
     comment claiming nova-3 mangles Kannada ("turning Kannada into English
     near-misses") and Sarvam's account was reported at 0 credits three days
     ago (2026-08-21) - both claims are tested fresh here, not trusted.
  3. LLM: Groq and OpenRouter both answer live (the fallback chain agent.py
     already builds stays as fallback per this pilot's direction - this just
     confirms both tiers are actually reachable before relying on it).

Run: python scripts/test_stt_tts_llm_live.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
from pathlib import Path

# Windows console defaults to cp1252, which cannot print Kannada script -
# reconfigure stdout to UTF-8 so transcripts print instead of crashing.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT.parent / ".env", override=False)

RESULTS: dict = {"tts": {}, "stt": {}, "llm": {}}

# A real clinical phrase Sakhi actually needs to hear back correctly:
# "blood pressure is one forty by ninety, and there is bleeding"
TEST_TEXT_KN = "ರಕ್ತದೊತ್ತಡ ನೂರ ನಲವತ್ತು ಒಂಬತ್ತು, ಮತ್ತು ರಕ್ತಸ್ರಾವ ಆಗ್ತಿದೆ"
EXPECTED_KEYWORDS = ["ನಲವತ್ತು", "ರಕ್ತಸ್ರಾವ"]  # "forty", "bleeding" - danger sign must survive


async def synth_vakyam() -> tuple[bytes, int]:
    """Returns (raw pcm16 bytes, sample_rate). Also records TTFB-equivalent."""
    import websockets

    api_key = os.getenv("VAKYAM_API_KEY")
    assert api_key, "VAKYAM_API_KEY not set"

    t0 = time.perf_counter()
    first_byte_t = None
    pcm_chunks: list[bytes] = []
    async with websockets.connect(
        "wss://api.vakyam.ai/v1/tts/websocket",
        additional_headers={"Authorization": f"Bearer {api_key}"},
        open_timeout=6.0,
    ) as ws:
        await ws.recv()
        await ws.send(json.dumps({
            "type": "config", "model_id": "raaga-v1", "voice": "Ananya",
            "language": "kn-IN", "output_format": "pcm",
            "sample_rate": 24000, "speed": 1.0,
        }))
        await ws.recv()
        connected_t = time.perf_counter()
        await ws.send(json.dumps({"type": "text", "text": TEST_TEXT_KN}))
        while True:
            msg = await ws.recv()
            if isinstance(msg, (bytes, bytearray)):
                if first_byte_t is None:
                    first_byte_t = time.perf_counter()
                pcm_chunks.append(bytes(msg))
                continue
            data = json.loads(msg)
            if data.get("type") == "end_of_utterance":
                break
            if data.get("type") == "error":
                raise RuntimeError(f"Vakyam error: {data}")
    total_t = time.perf_counter()

    ttfb_ms = (first_byte_t - connected_t) * 1000 if first_byte_t else None
    total_ms = (total_t - t0) * 1000
    RESULTS["tts"]["vakyam"] = {
        "ok": True, "ttfb_ms": round(ttfb_ms, 1) if ttfb_ms else None,
        "total_synth_ms": round(total_ms, 1),
        "audio_bytes": sum(len(c) for c in pcm_chunks),
    }
    print(f"[TTS] Vakyam/Ananya: TTFB={ttfb_ms:.1f}ms  total={total_ms:.1f}ms  "
          f"bytes={sum(len(c) for c in pcm_chunks)}")
    return b"".join(pcm_chunks), 24000


async def test_deepgram_stt(pcm: bytes, sample_rate: int) -> None:
    from livekit import rtc
    from livekit.plugins import deepgram

    key = os.getenv("DEEPGRAM_API_KEY")
    if not key:
        print("[STT] Deepgram: SKIPPED (no DEEPGRAM_API_KEY)")
        RESULTS["stt"]["deepgram"] = {"ok": False, "error": "no api key"}
        return

    frame = rtc.AudioFrame(
        data=pcm, sample_rate=sample_rate, num_channels=1,
        samples_per_channel=len(pcm) // 2,
    )
    from livekit.agents.utils import http_context

    dg = deepgram.STT(model="nova-3", language="kn", api_key=key)
    t0 = time.perf_counter()
    try:
        async with http_context.open():
            event = await dg.recognize(buffer=[frame])
        elapsed_ms = (time.perf_counter() - t0) * 1000
        text = event.alternatives[0].text if event.alternatives else ""
        hits = [k for k in EXPECTED_KEYWORDS if k in text]
        RESULTS["stt"]["deepgram"] = {
            "ok": True, "latency_ms": round(elapsed_ms, 1),
            "transcript": text, "expected_keyword_hits": hits,
            "keyword_hit_rate": f"{len(hits)}/{len(EXPECTED_KEYWORDS)}",
        }
        print(f"[STT] Deepgram nova-3: {elapsed_ms:.0f}ms  transcript={text!r}  "
              f"keyword_hits={hits}")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        RESULTS["stt"]["deepgram"] = {"ok": False, "latency_ms": round(elapsed_ms, 1), "error": str(e)}
        print(f"[STT] Deepgram nova-3: FAILED after {elapsed_ms:.0f}ms - {e}")


async def test_sarvam_stt(pcm: bytes, sample_rate: int) -> None:
    from livekit import rtc

    sys.path.insert(0, str(PROJECT_ROOT))
    from sarvam_wrappers import SarvamSTT

    key = os.getenv("SARVAM_API_KEY")
    if not key:
        print("[STT] Sarvam: SKIPPED (no SARVAM_API_KEY)")
        RESULTS["stt"]["sarvam"] = {"ok": False, "error": "no api key"}
        return

    stt_instance = SarvamSTT(api_key=key, language_code="kn-IN")
    stream = stt_instance.stream(language="kn-IN")

    # Push in ~20ms frames, matching how a real call feeds audio in.
    frame_samples = int(sample_rate * 0.02)
    bytes_per_frame = frame_samples * 2
    t0 = time.perf_counter()
    final_text = ""
    got_final = False
    error = None

    async def _pusher():
        for i in range(0, len(pcm) - bytes_per_frame, bytes_per_frame):
            chunk = pcm[i:i + bytes_per_frame]
            frame = rtc.AudioFrame(
                data=chunk, sample_rate=sample_rate, num_channels=1,
                samples_per_channel=len(chunk) // 2,
            )
            stream.push_frame(frame)
            await asyncio.sleep(0.02)
        stream.end_input()

    async def _reader():
        from livekit.agents import stt as stt_mod
        nonlocal final_text, got_final
        async for event in stream:
            if event.type == stt_mod.SpeechEventType.FINAL_TRANSCRIPT:
                if event.alternatives:
                    final_text = event.alternatives[0].text
                    got_final = True
                    break

    try:
        await asyncio.wait_for(asyncio.gather(_pusher(), _reader()), timeout=15.0)
    except Exception as e:
        error = str(e)
    finally:
        try:
            await stream.aclose()
        except Exception:
            pass

    elapsed_ms = (time.perf_counter() - t0) * 1000
    if error and not got_final:
        RESULTS["stt"]["sarvam"] = {"ok": False, "latency_ms": round(elapsed_ms, 1), "error": error}
        print(f"[STT] Sarvam saarika:v2.5: FAILED after {elapsed_ms:.0f}ms - {error}")
        return

    hits = [k for k in EXPECTED_KEYWORDS if k in final_text]
    RESULTS["stt"]["sarvam"] = {
        "ok": got_final, "latency_ms": round(elapsed_ms, 1),
        "transcript": final_text, "expected_keyword_hits": hits,
        "keyword_hit_rate": f"{len(hits)}/{len(EXPECTED_KEYWORDS)}",
    }
    print(f"[STT] Sarvam saarika:v2.5: {elapsed_ms:.0f}ms  transcript={final_text!r}  "
          f"keyword_hits={hits}")


async def test_groq_llm() -> None:
    from openai import AsyncOpenAI

    keys = [k.strip() for k in (os.getenv("GROQ_API_KEYS") or os.getenv("GROQ_API_KEY", "")).split(",") if k.strip()]
    if not keys:
        print("[LLM] Groq: SKIPPED (no key)")
        RESULTS["llm"]["groq"] = {"ok": False, "error": "no api key"}
        return
    client = AsyncOpenAI(api_key=keys[0], base_url="https://api.groq.com/openai/v1")
    t0 = time.perf_counter()
    try:
        resp = await client.chat.completions.create(
            model=os.getenv("GROQ_MODEL_T1", "openai/gpt-oss-120b"),
            messages=[{"role": "user", "content": "Reply with exactly one word: ready"}],
            # gpt-oss is a reasoning model - a tight token cap gets entirely
            # consumed by the hidden reasoning trace, leaving zero visible
            # output (this repo's own agent.py documents the same trap).
            # 200, matching build_llm()'s real production cap, not the 10
            # this test used on its first (failed) run.
            max_completion_tokens=200,
            reasoning_effort="low",
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        text = resp.choices[0].message.content
        RESULTS["llm"]["groq"] = {"ok": True, "latency_ms": round(elapsed_ms, 1), "reply": text}
        print(f"[LLM] Groq: {elapsed_ms:.0f}ms  reply={text!r}")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        RESULTS["llm"]["groq"] = {"ok": False, "latency_ms": round(elapsed_ms, 1), "error": str(e)}
        print(f"[LLM] Groq: FAILED after {elapsed_ms:.0f}ms - {e}")


async def test_openrouter_llm() -> None:
    from openai import AsyncOpenAI

    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        print("[LLM] OpenRouter: SKIPPED (no key)")
        RESULTS["llm"]["openrouter"] = {"ok": False, "error": "no api key"}
        return
    client = AsyncOpenAI(api_key=key, base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    t0 = time.perf_counter()
    try:
        resp = await client.chat.completions.create(
            model=os.getenv("OPENROUTER_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct"),
            messages=[{"role": "user", "content": "Reply with exactly one word: ready"}],
            max_tokens=10,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        text = resp.choices[0].message.content
        RESULTS["llm"]["openrouter"] = {"ok": True, "latency_ms": round(elapsed_ms, 1), "reply": text}
        print(f"[LLM] OpenRouter: {elapsed_ms:.0f}ms  reply={text!r}")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        RESULTS["llm"]["openrouter"] = {"ok": False, "latency_ms": round(elapsed_ms, 1), "error": str(e)}
        print(f"[LLM] OpenRouter: FAILED after {elapsed_ms:.0f}ms - {e}")


async def main():
    print("=" * 70)
    print("LIVE PROVIDER TEST -", time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 70)

    print("\n--- TTS: synthesizing test phrase with Vakyam (3 runs, first is cold) ---")
    pcm = sr = None
    ttfbs = []
    for i in range(3):
        pcm, sr = await synth_vakyam()
        ttfbs.append(RESULTS["tts"]["vakyam"]["ttfb_ms"])
    RESULTS["tts"]["vakyam"]["ttfb_ms_all_runs"] = ttfbs
    RESULTS["tts"]["vakyam"]["ttfb_ms_warm_median"] = sorted(ttfbs[1:])[len(ttfbs[1:]) // 2] if len(ttfbs) > 1 else ttfbs[0]
    print(f"    TTFB across runs: {ttfbs}  (warm median: {RESULTS['tts']['vakyam']['ttfb_ms_warm_median']}ms)")

    print("\n--- STT: feeding the SAME synthesized audio to each provider ---")
    print(f"    input text: {TEST_TEXT_KN}")
    await test_deepgram_stt(pcm, sr)
    if os.getenv("SKIP_SARVAM_RETEST") != "1":
        await test_sarvam_stt(pcm, sr)
    else:
        print("[STT] Sarvam: SKIPPED (SKIP_SARVAM_RETEST=1 - already confirmed credit-exhausted)")
        RESULTS["stt"]["sarvam"] = {"ok": False, "error": "credits exhausted (confirmed live, 2026-09-02)"}

    print("\n--- LLM: liveness + TTFT ---")
    await asyncio.gather(test_groq_llm(), test_openrouter_llm())

    out_path = PROJECT_ROOT / "logs" / "live_provider_test_20260902.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFull results written to {out_path}")
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
