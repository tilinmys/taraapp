"""Smoke test for the running Indic STT service (step 5 of stt_service/README.md).

    stt_service\\.venv\\Scripts\\python stt_service\\smoke_test.py [http://127.0.0.1:8795]

Checks /health, then sends a 1.5 s 16 kHz WAV (a tone: the transcript itself
does not matter) to /api/stt/raw in kn, hi and en, and checks every reply has
the shape the gateway and the app read. Prints OK, or what failed (exit 1).
Standard library only.
"""

from __future__ import annotations

import io
import json
import math
import struct
import sys
import urllib.request
import wave

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8795").rstrip("/")
KEYS = {"text", "en", "ms", "provider", "lang", "lid_confidence", "mode", "seconds"}


def tone_wav(seconds: float = 1.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / 16000))) for i in range(int(16000 * seconds))))
    return buf.getvalue()


def main() -> int:
    try:
        health = json.loads(urllib.request.urlopen(f"{BASE}/health", timeout=10).read())
    except Exception as exc:
        print(f"FAIL: service not reachable at {BASE} ({exc}). Start it: stt_service\\run.cmd")
        return 1
    if not health.get("loaded"):
        print(f"FAIL: model not loaded: {health}")
        return 1
    print(f"health: {health['model']} on {health['device']}, langs {health['langs']}")
    body = tone_wav()
    for lang in ("kn", "hi", "en"):
        req = urllib.request.Request(f"{BASE}/api/stt/raw?lang={lang}", data=body, method="POST", headers={"Content-Type": "audio/wav"})
        try:
            out = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as exc:
            print(f"FAIL: /api/stt/raw?lang={lang}: {exc}")
            return 1
        missing = KEYS - set(out)
        if missing or out["provider"] != "indic" or out["lang"] != lang:
            print(f"FAIL: lang={lang} reply {out} (missing {sorted(missing)})")
            return 1
        print(f"  {lang}: {out['ms']} ms, mode {out['mode']}")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
