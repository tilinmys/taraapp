"""Read-only Sarvam STT wire-protocol probe using Sakhi's cached greeting.

Designed for one-off execution through SSM.  It reads provider credentials but
never prints them, does not contain patient audio, and changes no host state.
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import io
import json
import os
import time
import wave
from pathlib import Path
from urllib.parse import urlencode

import websockets


def _load_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"").strip("'")
    except OSError:
        pass
    return values


def _fixture_pcm() -> bytes:
    candidates = (
        "/opt/sakhi/assets/audio/greetings/shreya-kn-8525ff1d-p095.wav",
        "/opt/sakhi/assets/audio/greetings/shreya-kn-8525ff1d-p100.wav",
    )
    source = next((path for path in candidates if Path(path).exists()), None)
    if source is None:
        raise RuntimeError("non-patient greeting fixture is missing")
    with wave.open(source, "rb") as wav_file:
        rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        width = wav_file.getsampwidth()
        pcm = wav_file.readframes(wav_file.getnframes())
    if channels == 2:
        pcm = audioop.tomono(pcm, width, 0.5, 0.5)
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if rate != 16000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 16000, None)
    return pcm


def _wav_chunk(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm)
    return out.getvalue()


async def _connect(uri: str, key: str):
    kwargs = {"open_timeout": 8, "close_timeout": 2, "ping_interval": 10}
    if "additional_headers" in __import__("inspect").signature(websockets.connect).parameters:
        kwargs["additional_headers"] = {"api-subscription-key": key}
    else:
        kwargs["extra_headers"] = {"api-subscription-key": key}
    return websockets.connect(uri, **kwargs)


async def _probe(name: str, *, current: bool, key: str, pcm: bytes) -> dict:
    query = {"language-code": "kn-IN", "model": "saaras:v3"}
    if current:
        query.update(
            {
                "mode": "codemix",
                "sample_rate": "16000",
                "vad_signals": "true",
                "high_vad_sensitivity": "true",
                "flush_signal": "true",
            }
        )
    uri = "wss://api.sarvam.ai/speech-to-text/ws?" + urlencode(query)
    started = time.perf_counter()
    result = {
        "case": name,
        "ok": False,
        "events": 0,
        "event_shapes": [],
        "transcript_chars": 0,
    }
    try:
        connector = await _connect(uri, key)
        async with connector as ws:
            result["connect_ms"] = round((time.perf_counter() - started) * 1000, 2)
            if not current:
                await ws.send(
                    json.dumps(
                        {
                            "type": "config",
                            "data": {
                                "model": "saaras:v3",
                                "mode": "codemix",
                                "language_code": "kn-IN",
                            },
                        }
                    )
                )

            async def receive() -> None:
                deadline = time.perf_counter() + 12
                while time.perf_counter() < deadline:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    response = json.loads(message)
                    result["events"] += 1
                    data_keys = []
                    if isinstance(response.get("data"), dict):
                        data_keys = sorted(str(key) for key in response["data"].keys())
                    result["event_shapes"].append(
                        {
                            "type": str(response.get("type", ""))[:40],
                            "top_keys": sorted(str(key) for key in response.keys()),
                            "data_keys": data_keys,
                            "signal_type": str(
                                response.get("data", {}).get("signal_type", "")
                                if isinstance(response.get("data"), dict)
                                else ""
                            )[:40],
                            "error_present": bool(
                                response.get("error")
                                or (
                                    response.get("data", {}).get("error")
                                    if isinstance(response.get("data"), dict)
                                    else None
                                )
                            ),
                            "error_message": str(
                                response.get("data", {}).get("message", "")
                                if isinstance(response.get("data"), dict)
                                else ""
                            )[:240],
                        }
                    )
                    data = response.get("data") if isinstance(response.get("data"), dict) else response
                    transcript = data.get("transcript", "") if isinstance(data, dict) else ""
                    if transcript:
                        result["transcript_chars"] = len(transcript)
                        result["final_ms"] = round((time.perf_counter() - started) * 1000, 2)
                        result["ok"] = True
                        return

            receiver = asyncio.create_task(receive())
            for offset in range(0, len(pcm), 3200):
                chunk = pcm[offset : offset + 3200]
                wire = chunk if current else _wav_chunk(chunk)
                payload = {
                    "audio": {
                        "data": base64.b64encode(wire).decode("ascii"),
                        "encoding": "audio/wav",
                        "sample_rate": 16000,
                    }
                }
                if not current:
                    payload["type"] = "audio"
                await ws.send(json.dumps(payload))
                await asyncio.sleep(0.1)
                if receiver.done():
                    break
            if current and not receiver.done():
                await ws.send(json.dumps({"type": "flush"}))
            await asyncio.wait_for(receiver, timeout=13)
            if not result["ok"]:
                result["error"] = "timeout_without_transcript"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return result


async def main() -> None:
    env = _load_env("/opt/sakhi/.env")
    env.update(_load_env("/etc/sakhi.env"))
    env.update(os.environ)
    key = env.get("SARVAM_API_KEY", "")
    if not key:
        raise RuntimeError("SARVAM_API_KEY is unavailable")
    pcm = _fixture_pcm()
    selected = os.getenv("PROBE_CASE", "both").strip().lower()
    results = []
    if selected in {"both", "legacy"}:
        results.append(
            await _probe("deployed_legacy_contract", current=False, key=key, pcm=pcm)
        )
    if selected in {"both", "current"}:
        results.append(
            await _probe("repaired_current_contract", current=True, key=key, pcm=pcm)
        )
    print(json.dumps({"fixture": "cached_sakhi_greeting", "results": results}))


if __name__ == "__main__":
    asyncio.run(main())
