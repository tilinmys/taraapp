"""Prerecord every fixed line Sakhi says, in Kannada and Hindi, once.

    python scripts/prerecord_sakhi.py [--voice Kaveri] [--force]

Why: the check-in's lines never change (a greeting, every visit's questions, the
re-ask, the "please say it again", the danger warning, the closing). Making
them with TTS on every check-in costs provider requests and adds network
waits. Recorded once, they ship inside the app and play instantly, offline,
with zero TTS calls at run time.

The texts are read from the app's own sources, never retyped here:
  app/src/data/mockScreening.ts  the question bank (questionKn / questionHi)
                                 and SAKHI_REPLIES (danger, urgent, done)
  app/src/i18n/strings.ts        vGreetingVoice, vReask, vReprompt,
                                 vAskName, vAskAge, vAskDiastolic (micro-prompts)
so a recording always matches what the screen shows. Output:
  app/assets/voice/<lang>/<key>.wav        16 kHz mono PCM16 (Gnani, female voice)
  app/assets/voice/manifest.json           key -> text, file, duration, voice
  app/src/voice/prerecorded.generated.ts   static require()s for Metro
A line whose text later changes no longer matches its recording; the app
then falls back to live TTS for that line only, until this is run again.
Unchanged lines are not re-recorded (use --force to redo all).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
OUT = APP / "assets" / "voice"
GEN = APP / "src" / "voice" / "prerecorded.generated.ts"
LANGS = ("kn", "hi")


def ts_unquote(s: str) -> str:
    return s.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n")


def string_block(src: str, lang: str) -> str:
    start = src.index(f"const {lang}: Strings = {{")
    end = src.index("\n};", start)
    return src[start:end]


def string_key(block: str, key: str) -> str:
    m = re.search(rf"\n  {key}: '((?:[^'\\]|\\.)*)',", block)
    if not m:
        raise SystemExit(f"strings.ts: {key} not found")
    return ts_unquote(m.group(1))


# The danger-sign follow-ups ("and blurred vision or swelling?"), built
# exactly like app/src/voice/nlu/followup.ts: every proper subset of each
# question's signs, in the question's order, joined with vOr into
# vAlsoSigns. voice/__tests__/prerecorded.test.ts fails if the two drift.
QUESTION_SIGNS = {
    "bleedDischarge": ["bleeding", "discharge"],
    "feverPain": ["fever", "vomiting", "abdPain"],
    "bleedFluid": ["bleeding", "leaking"],
    "bleedLeak": ["bleeding", "leaking"],
    "warning": ["headache", "vision", "swellFace"],
    "breathing": ["breathless", "tired", "palpitations"],
}


def sign_followups(block: str) -> list[tuple[str, str]]:
    template, joiner = string_key(block, "vAlsoSigns"), string_key(block, "vOr")
    out, seen = [], set()
    for signs in QUESTION_SIGNS.values():
        for mask in range(1, (1 << len(signs)) - 1):
            rest = [sg for i, sg in enumerate(signs) if mask & (1 << i)]
            words = joiner.join(string_key(block, "sg" + sg[0].upper() + sg[1:]) for sg in rest)
            line = template.replace("{signs}", words)
            if line in seen:  # two questions asking the same signs: one recording
                continue
            seen.add(line)
            out.append((f"also_{'_'.join(rest)}", line))
    return out


def collect() -> dict[str, list[tuple[str, str]]]:
    screening = (APP / "src" / "data" / "mockScreening.ts").read_text(encoding="utf-8")
    strings = (APP / "src" / "i18n" / "strings.ts").read_text(encoding="utf-8")
    questions = re.findall(r"\{ id: '([A-Za-z0-9]+)'.*?questionKn: '((?:[^'\\]|\\.)*)', questionHi: '((?:[^'\\]|\\.)*)'", screening)
    if len(questions) < 30:
        raise SystemExit(f"expected the question bank in mockScreening.ts, found {len(questions)} questions")
    replies = {}
    for lang in LANGS:
        m = re.search(rf"\n  {lang}: \{{\n    ok: '((?:[^'\\]|\\.)*)',\n    danger: '((?:[^'\\]|\\.)*)',\n    done: '((?:[^'\\]|\\.)*)',\n    urgent: '((?:[^'\\]|\\.)*)',", screening)
        if not m:
            raise SystemExit(f"SAKHI_REPLIES.{lang} not found")
        replies[lang] = {"ok": ts_unquote(m.group(1)), "danger": ts_unquote(m.group(2)), "done": ts_unquote(m.group(3)), "urgent": ts_unquote(m.group(4))}
    lines: dict[str, list[tuple[str, str]]] = {}
    for lang in LANGS:
        block = string_block(strings, lang)
        out = [
            ("greeting", string_key(block, "vGreetingVoice")),
            ("reask", string_key(block, "vReask")),
            ("reprompt", string_key(block, "vReprompt")),
            ("ask_how_many", string_key(block, "vAskHowMany")),
            ("ask_date", string_key(block, "vAskDate")),
            ("waiting", string_key(block, "vWaiting")),
            ("away", string_key(block, "vAway")),
            ("ok", replies[lang]["ok"]),
            ("danger", replies[lang]["danger"]),
            ("urgent", replies[lang]["urgent"]),
            ("done", replies[lang]["done"]),
        ]
        out += [(qid, ts_unquote(kn if lang == "kn" else hi)) for qid, kn, hi in questions]
        out += sign_followups(block)
        lines[lang] = out
    return lines


def duration_ms(wav: bytes) -> int:
    with wave.open(io.BytesIO(wav)) as w:
        return round(w.getnframes() / w.getframerate() * 1000)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default=os.getenv("SAKHI_APP_GNANI_VOICE", "Kaveri"), help="Gnani voice (female; Sakhi's voice is Kaveri)")
    ap.add_argument("--force", action="store_true", help="re-record lines whose text did not change")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "scripts"))
    from sakhi_gateway_dev import load_env  # noqa: E402

    load_env(ROOT / ".env")
    os.environ["SAKHI_APP_GNANI_VOICE"] = args.voice
    sys.path.insert(0, str(ROOT))
    import sakhi_gateway as gw  # noqa: E402  (reads the voice at import)

    if gw.provider() != "gnani":
        raise SystemExit("GNANI_API_KEY is not set in .env: cannot record Sakhi's voice")

    lines = collect()
    manifest_path = OUT / "manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest: dict = {"voice": args.voice, "provider": "gnani", "sample_rate": 16000, "lines": {}}
    made = kept = 0
    for lang, items in lines.items():
        (OUT / lang).mkdir(parents=True, exist_ok=True)
        manifest["lines"][lang] = {}
        for key, text in items:
            path = OUT / lang / f"{key}.wav"
            digest = hashlib.sha1(f"{args.voice}|{lang}|{text}".encode()).hexdigest()[:12]
            prev = old.get("lines", {}).get(lang, {}).get(key)
            if not args.force and prev and prev.get("hash") == digest and path.exists():
                manifest["lines"][lang][key] = prev
                kept += 1
                continue
            for attempt in range(4):
                try:
                    audio = gw.tts(text, lang, "now")
                    break
                except gw.GatewayError as e:
                    if attempt == 3:
                        raise
                    print(f"  retry {lang}/{key}: {e.message}")
                    time.sleep(1.5 * (attempt + 1))
            path.write_bytes(audio)
            ms = duration_ms(audio)
            manifest["lines"][lang][key] = {"text": text, "file": f"{lang}/{key}.wav", "ms": ms, "bytes": len(audio), "hash": digest}
            made += 1
            print(f"  {lang}/{key}.wav  {ms / 1000:.1f} s  {len(audio) // 1024} KB")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # Metro bundles only what is require()d with a literal path: generate them.
    rows = []
    for lang, entries in manifest["lines"].items():
        body = ",\n".join(
            f"    {key}: {{ text: {json.dumps(e['text'], ensure_ascii=False)}, ms: {e['ms']}, asset: require('../../assets/voice/{e['file']}') }}"
            for key, e in entries.items()
        )
        rows.append(f"  {lang}: {{\n{body},\n  }}")
    GEN.write_text(
        "// GENERATED by scripts/prerecord_sakhi.py: do not edit by hand.\n"
        f"// Voice: Gnani {args.voice} (female), 16 kHz mono. Re-run the script after changing a line.\n"
        "\n"
        "export type PrerecordedLine = { text: string; ms: number; asset: number };\n\n"
        "export const PRERECORDED: Record<'kn' | 'hi', Record<string, PrerecordedLine>> = {\n" + ",\n".join(rows) + ",\n};\n",
        encoding="utf-8",
    )
    total = sum(e["bytes"] for l in manifest["lines"].values() for e in l.values())
    print(f"Done: {made} recorded, {kept} unchanged, {total // 1024} KB total, voice {args.voice}")


if __name__ == "__main__":
    main()
