"""Comprehensive live provider latency benchmark, 2026-09-04.

Extends scripts/test_stt_tts_llm_live.py (2026-09-02) into a full sweep
across every TTS/STT/LLM provider this project actually has credentials
for, run against REAL APIs - answering "which combination is fastest" with
measured numbers instead of a brief's claimed numbers.

Reuses this project's own production builder functions where practical
(agent.py's _build_gnani_tts, sarvam_wrappers.SarvamSTT) so what's measured
here matches what a real call would actually construct.

Run: python scripts/latency_benchmark_20260904.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT.parent / ".env", override=False)

RESULTS: dict = {"tts": {}, "stt": {}, "llm": {}}

TEST_TEXT_KN = "ರಕ್ತದೊತ್ತಡ ನೂರ ನಲವತ್ತು ಒಂಬತ್ತು, ಮತ್ತು ರಕ್ತಸ್ರಾವ ಆಗ್ತಿದೆ"
EXPECTED_KEYWORDS = ["ನಲವತ್ತು", "ರಕ್ತಸ್ರಾವ"]


# ---------------------------------------------------------------- TTS -----

async def bench_vakyam() -> tuple[bytes, int] | None:
    import websockets

    api_key = os.getenv("VAKYAM_API_KEY")
    if not api_key:
        print("[TTS] Vakyam: SKIPPED (no key)")
        return None
    t0 = time.perf_counter()
    first_byte_t = None
    pcm_chunks: list[bytes] = []
    try:
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
        ttfb_ms = (first_byte_t - connected_t) * 1000 if first_byte_t else None
        total_ms = (time.perf_counter() - t0) * 1000
        RESULTS["tts"]["vakyam"] = {
            "ok": True, "ttfb_ms": round(ttfb_ms, 1) if ttfb_ms else None,
            "total_ms": round(total_ms, 1),
        }
        print(f"[TTS] Vakyam/Ananya:   TTFB={ttfb_ms:.1f}ms  total={total_ms:.1f}ms")
        return b"".join(pcm_chunks), 24000
    except Exception as e:
        RESULTS["tts"]["vakyam"] = {"ok": False, "error": str(e)}
        print(f"[TTS] Vakyam/Ananya:   FAILED - {e}")
        return None


async def bench_gnani() -> tuple[bytes, int] | None:
    """Reuses agent.py's own _build_gnani_tts() - same REST-forced config a
    real call uses (see agent.py's capabilities-override comment)."""
    key = os.getenv("GNANI_API_KEY")
    if not key:
        print("[TTS] Gnani: SKIPPED (no key)")
        return None
    try:
        import agent
        from livekit.agents.utils import http_context

        provider = agent._build_gnani_tts(None)
        t0 = time.perf_counter()
        first_byte_t = None
        pcm_chunks: list[bytes] = []
        async with http_context.open():
            stream = provider.synthesize(TEST_TEXT_KN)
            async for event in stream:
                if first_byte_t is None:
                    first_byte_t = time.perf_counter()
                frame = getattr(event, "frame", None)
                if frame is not None:
                    pcm_chunks.append(bytes(frame.data))
        ttfb_ms = (first_byte_t - t0) * 1000 if first_byte_t else None
        total_ms = (time.perf_counter() - t0) * 1000
        RESULTS["tts"]["gnani"] = {
            "ok": True, "ttfb_ms": round(ttfb_ms, 1) if ttfb_ms else None,
            "total_ms": round(total_ms, 1),
        }
        print(f"[TTS] Gnani/Kaveri:    TTFB={ttfb_ms:.1f}ms  total={total_ms:.1f}ms")
        return b"".join(pcm_chunks), 16000
    except Exception as e:
        RESULTS["tts"]["gnani"] = {"ok": False, "error": str(e)}
        print(f"[TTS] Gnani/Kaveri:    FAILED - {e}")
        return None


async def bench_sarvam() -> tuple[bytes, int] | None:
    key = os.getenv("SARVAM_API_KEY")
    if not key:
        print("[TTS] Sarvam: SKIPPED (no key)")
        return None
    try:
        from sarvam_wrappers import SarvamTTS
        from livekit.agents.utils import http_context

        # bulbul:v2 is deprecated as of this run (real 400 from the API,
        # 2026-09-04) - v3 is the live model now.
        provider = SarvamTTS(
            api_key=key, model="bulbul:v3", speaker="manisha",
            target_language_code="kn-IN", pace=1.0,
        )
        t0 = time.perf_counter()
        first_byte_t = None
        pcm_chunks: list[bytes] = []
        async with http_context.open():
            stream = provider.synthesize(TEST_TEXT_KN)
            async for event in stream:
                if first_byte_t is None:
                    first_byte_t = time.perf_counter()
                frame = getattr(event, "frame", None)
                if frame is not None:
                    pcm_chunks.append(bytes(frame.data))
        ttfb_ms = (first_byte_t - t0) * 1000 if first_byte_t else None
        total_ms = (time.perf_counter() - t0) * 1000
        RESULTS["tts"]["sarvam"] = {
            "ok": True, "ttfb_ms": round(ttfb_ms, 1) if ttfb_ms else None,
            "total_ms": round(total_ms, 1),
        }
        print(f"[TTS] Sarvam/manisha:  TTFB={ttfb_ms:.1f}ms  total={total_ms:.1f}ms")
        return b"".join(pcm_chunks), 16000
    except Exception as e:
        RESULTS["tts"]["sarvam"] = {"ok": False, "error": str(e)}
        print(f"[TTS] Sarvam/manisha:  FAILED - {e}")
        return None


async def bench_smallest_ai() -> tuple[bytes, int] | None:
    key = os.getenv("SMALLEST_API_KEY")
    if not key:
        print("[TTS] Smallest.ai: SKIPPED (no key)")
        return None
    try:
        from livekit.plugins import smallestai
        from livekit.agents.utils import http_context

        provider = smallestai.TTS(
            api_key=key, model="lightning_v3.1_pro", voice_id="meher",
            sample_rate=24000, speed=1.0, language="kn",
        )
        t0 = time.perf_counter()
        first_byte_t = None
        pcm_chunks: list[bytes] = []
        async with http_context.open():
            stream = provider.synthesize(TEST_TEXT_KN)
            async for event in stream:
                if first_byte_t is None:
                    first_byte_t = time.perf_counter()
                frame = getattr(event, "frame", None)
                if frame is not None:
                    pcm_chunks.append(bytes(frame.data))
        ttfb_ms = (first_byte_t - t0) * 1000 if first_byte_t else None
        total_ms = (time.perf_counter() - t0) * 1000
        RESULTS["tts"]["smallest_ai"] = {
            "ok": True, "ttfb_ms": round(ttfb_ms, 1) if ttfb_ms else None,
            "total_ms": round(total_ms, 1),
        }
        print(f"[TTS] Smallest/meher:  TTFB={ttfb_ms:.1f}ms  total={total_ms:.1f}ms")
        return b"".join(pcm_chunks), 24000
    except Exception as e:
        RESULTS["tts"]["smallest_ai"] = {"ok": False, "error": str(e)}
        print(f"[TTS] Smallest/meher:  FAILED - {e}")
        return None


# ---------------------------------------------------------------- STT -----

async def bench_deepgram_stt(pcm: bytes, sample_rate: int) -> None:
    from livekit import rtc
    from livekit.plugins import deepgram
    from livekit.agents.utils import http_context

    key = os.getenv("DEEPGRAM_API_KEY")
    if not key:
        print("[STT] Deepgram: SKIPPED (no key)")
        return
    frame = rtc.AudioFrame(data=pcm, sample_rate=sample_rate, num_channels=1, samples_per_channel=len(pcm) // 2)
    dg = deepgram.STT(model="nova-3", language="kn", api_key=key)
    t0 = time.perf_counter()
    try:
        async with http_context.open():
            event = await dg.recognize(buffer=[frame])
        elapsed_ms = (time.perf_counter() - t0) * 1000
        text = event.alternatives[0].text if event.alternatives else ""
        hits = [k for k in EXPECTED_KEYWORDS if k in text]
        RESULTS["stt"]["deepgram"] = {"ok": True, "latency_ms": round(elapsed_ms, 1), "transcript": text, "hits": hits}
        print(f"[STT] Deepgram nova-3: {elapsed_ms:.0f}ms  transcript={text!r}  hits={hits}")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        RESULTS["stt"]["deepgram"] = {"ok": False, "latency_ms": round(elapsed_ms, 1), "error": str(e)}
        print(f"[STT] Deepgram nova-3: FAILED after {elapsed_ms:.0f}ms - {e}")


async def bench_gnani_stt(pcm: bytes, sample_rate: int) -> None:
    from livekit import rtc
    from livekit.plugins import gnani
    from livekit.agents import stt as stt_mod
    from livekit.agents.utils import http_context

    key = os.getenv("GNANI_API_KEY")
    if not key:
        print("[STT] Gnani: SKIPPED (no key)")
        return
    provider = gnani.STT(api_key=key, language="kn-IN", sample_rate=sample_rate)
    t0 = time.perf_counter()
    final_text = ""
    got_final = False
    try:
        async with http_context.open():
            stream = provider.stream()
            frame_samples = int(sample_rate * 0.02)
            bytes_per_frame = frame_samples * 2

            async def _pusher():
                for i in range(0, len(pcm) - bytes_per_frame, bytes_per_frame):
                    chunk = pcm[i:i + bytes_per_frame]
                    frame = rtc.AudioFrame(data=chunk, sample_rate=sample_rate, num_channels=1, samples_per_channel=len(chunk) // 2)
                    stream.push_frame(frame)
                    await asyncio.sleep(0.02)
                stream.end_input()

            async def _reader():
                nonlocal final_text, got_final
                async for event in stream:
                    if event.type == stt_mod.SpeechEventType.FINAL_TRANSCRIPT:
                        if event.alternatives:
                            final_text = event.alternatives[0].text
                            got_final = True
                            break

            await asyncio.wait_for(asyncio.gather(_pusher(), _reader()), timeout=15.0)
            await stream.aclose()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        hits = [k for k in EXPECTED_KEYWORDS if k in final_text]
        RESULTS["stt"]["gnani"] = {"ok": got_final, "latency_ms": round(elapsed_ms, 1), "transcript": final_text, "hits": hits}
        print(f"[STT] Gnani:           {elapsed_ms:.0f}ms  transcript={final_text!r}  hits={hits}")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        RESULTS["stt"]["gnani"] = {"ok": False, "latency_ms": round(elapsed_ms, 1), "error": str(e)}
        print(f"[STT] Gnani:           FAILED after {elapsed_ms:.0f}ms - {e}")


async def bench_sarvam_stt(pcm: bytes, sample_rate: int) -> None:
    from livekit import rtc
    from sarvam_wrappers import SarvamSTT
    from livekit.agents import stt as stt_mod

    key = os.getenv("SARVAM_API_KEY")
    if not key:
        print("[STT] Sarvam: SKIPPED (no key)")
        return
    stt_instance = SarvamSTT(api_key=key, language_code="kn-IN")
    stream = stt_instance.stream(language="kn-IN")
    frame_samples = int(sample_rate * 0.02)
    bytes_per_frame = frame_samples * 2
    t0 = time.perf_counter()
    final_text = ""
    got_final = False
    error = None

    async def _pusher():
        for i in range(0, len(pcm) - bytes_per_frame, bytes_per_frame):
            chunk = pcm[i:i + bytes_per_frame]
            frame = rtc.AudioFrame(data=chunk, sample_rate=sample_rate, num_channels=1, samples_per_channel=len(chunk) // 2)
            stream.push_frame(frame)
            await asyncio.sleep(0.02)
        stream.end_input()

    async def _reader():
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
        print(f"[STT] Sarvam saarika:  FAILED after {elapsed_ms:.0f}ms - {error}")
        return
    hits = [k for k in EXPECTED_KEYWORDS if k in final_text]
    RESULTS["stt"]["sarvam"] = {"ok": got_final, "latency_ms": round(elapsed_ms, 1), "transcript": final_text, "hits": hits}
    print(f"[STT] Sarvam saarika:  {elapsed_ms:.0f}ms  transcript={final_text!r}  hits={hits}")


# ---------------------------------------------------------------- LLM -----

async def bench_groq_model(model: str, key_index: int = 0) -> None:
    from openai import AsyncOpenAI

    keys = [k.strip() for k in (os.getenv("GROQ_API_KEYS") or os.getenv("GROQ_API_KEY", "")).split(",") if k.strip()]
    if not keys:
        print(f"[LLM] Groq/{model}: SKIPPED (no key)")
        return
    key = keys[key_index % len(keys)]
    client = AsyncOpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
    t0 = time.perf_counter()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly one word: ready"}],
            max_completion_tokens=200,
            reasoning_effort="low",
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        text = resp.choices[0].message.content
        RESULTS["llm"][f"groq/{model}"] = {"ok": True, "latency_ms": round(elapsed_ms, 1), "reply": text}
        print(f"[LLM] Groq/{model}: {elapsed_ms:.0f}ms  reply={text!r}")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        RESULTS["llm"][f"groq/{model}"] = {"ok": False, "latency_ms": round(elapsed_ms, 1), "error": str(e)}
        print(f"[LLM] Groq/{model}: FAILED after {elapsed_ms:.0f}ms - {e}")


async def bench_openrouter_model(model: str) -> None:
    from openai import AsyncOpenAI

    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        print(f"[LLM] OpenRouter/{model}: SKIPPED (no key)")
        return
    client = AsyncOpenAI(api_key=key, base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    t0 = time.perf_counter()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly one word: ready"}],
            max_tokens=10,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        text = resp.choices[0].message.content
        RESULTS["llm"][f"openrouter/{model}"] = {"ok": True, "latency_ms": round(elapsed_ms, 1), "reply": text}
        print(f"[LLM] OpenRouter/{model}: {elapsed_ms:.0f}ms  reply={text!r}")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        RESULTS["llm"][f"openrouter/{model}"] = {"ok": False, "latency_ms": round(elapsed_ms, 1), "error": str(e)}
        print(f"[LLM] OpenRouter/{model}: FAILED after {elapsed_ms:.0f}ms - {e}")


async def main():
    print("=" * 78)
    print("LATENCY BENCHMARK -", time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 78)

    print("\n--- TTS: TTFB for every configured provider (same test phrase) ---")
    vakyam_audio = await bench_vakyam()
    gnani_audio = await bench_gnani()
    sarvam_audio = await bench_sarvam()
    smallest_audio = await bench_smallest_ai()

    # STT needs real audio - reuse whichever TTS synth succeeded first.
    # Gnani STT only accepts 8k/16k/44.1k/48k, so it gets its own (16kHz)
    # source rather than Vakyam's 24kHz output.
    stt_source = vakyam_audio or sarvam_audio or smallest_audio
    if stt_source:
        pcm, sr = stt_source
        print(f"\n--- STT: feeding the same synthesized audio to each provider ---")
        await bench_deepgram_stt(pcm, sr)
        await bench_sarvam_stt(pcm, sr)
    else:
        print("\n--- STT: Deepgram/Sarvam SKIPPED (no 24kHz-capable TTS synth succeeded) ---")
    if gnani_audio:
        await bench_gnani_stt(*gnani_audio)
    else:
        print("[STT] Gnani: SKIPPED (no Gnani-synthesized 16kHz audio available)")

    print("\n--- LLM: TTFT-proxy (single non-streamed completion) across models ---")
    await asyncio.gather(
        bench_groq_model("openai/gpt-oss-120b", 0),
        bench_groq_model("openai/gpt-oss-120b", 1),
        bench_groq_model("openai/gpt-oss-20b", 0),
        bench_groq_model("qwen/qwen3.6-27b", 0),
        bench_groq_model("qwen/qwen3.8-27b", 0),
        bench_openrouter_model("meta-llama/llama-3.3-70b-instruct"),
    )

    out_path = PROJECT_ROOT / "logs" / "latency_benchmark_20260904.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFull results written to {out_path}")
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
