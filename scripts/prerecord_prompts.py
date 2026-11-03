"""Record every prompt the zero-TTS phone turn loop plays, once.

    python scripts/prerecord_prompts.py [--voice Kaveri] [--force] [--lang kn]

The phone agent (agent.py, VOICE_ZERO_TTS_MODE) answers each turn with a
pre-recorded WAV instead of LLM + TTS. This writes them:

  assets/audio/prompts/<lang>/<qid>_full.wav     every intake question
  assets/audio/prompts/<lang>/q01_ask_name.wav   micro-prompts (one slot)
  assets/audio/prompts/<lang>/emergency_referral.wav
  assets/audio/prompts/manifest.json             key -> text, ms, hash

The texts come from sakhi_intake.py and sakhi_slot_engine.PROMPT_TEXT, never
retyped here, and the list from sakhi_slot_engine.all_prompt_paths(), the same
list the agent checks before it turns zero-TTS on. Unchanged lines are not
re-recorded. Voice: Gnani Kaveri (female), 16 kHz mono PCM16, the same voice
as the app's recordings (scripts/prerecord_sakhi.py).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "audio" / "prompts"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default=os.getenv("SAKHI_APP_GNANI_VOICE", "Kaveri"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--lang", action="append", choices=("kn", "hi"))
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "scripts"))
    from sakhi_gateway_dev import load_env  # noqa: E402

    load_env(ROOT / ".env")
    os.environ["SAKHI_APP_GNANI_VOICE"] = args.voice
    sys.path.insert(0, str(ROOT))
    import sakhi_gateway as gw  # noqa: E402
    from sakhi_slot_engine import PROMPT_DIR, all_prompt_paths, prompt_text  # noqa: E402

    if gw.provider() != "gnani":
        raise SystemExit("GNANI_API_KEY is not set in .env: cannot record Sakhi's voice")

    manifest_path = OUT / "manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest: dict = {"voice": args.voice, "provider": "gnani", "sample_rate": 16000, "prompts": dict(old.get("prompts", {}))}
    made = kept = 0
    for lang in args.lang or ("kn", "hi"):
        for rel in all_prompt_paths(lang):
            key = rel[len(PROMPT_DIR) + 1:]
            text = prompt_text(rel, lang)
            if not text:
                raise SystemExit(f"no text for {key}")
            path = OUT / key
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha1(f"{args.voice}|{lang}|{text}".encode()).hexdigest()[:12]
            prev = old.get("prompts", {}).get(key)
            if not args.force and prev and prev.get("hash") == digest and path.exists():
                kept += 1
                continue
            for attempt in range(4):
                try:
                    audio = gw.tts(text, lang, "now")
                    break
                except gw.GatewayError as e:
                    if attempt == 3:
                        raise
                    print(f"  retry {key}: {e.message}")
                    time.sleep(1.5 * (attempt + 1))
            path.write_bytes(audio)
            with wave.open(io.BytesIO(audio)) as w:
                ms = round(w.getnframes() / w.getframerate() * 1000)
            manifest["prompts"][key] = {"text": text, "ms": ms, "bytes": len(audio), "hash": digest}
            made += 1
            print(f"  {key}  {ms / 1000:.1f} s")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(p["bytes"] for p in manifest["prompts"].values())
    print(f"Done: {made} recorded, {kept} unchanged, {total // 1024} KB total")


if __name__ == "__main__":
    main()
