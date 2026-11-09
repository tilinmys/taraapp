"""Send every test answer through the SAME path the phone uses, and score it.

    python scripts/stt_eval/run_e2e.py [base_url]

base_url defaults to the Expo tunnel in app/tunnel-url.txt (exp:// -> https://):
phone path = <tunnel>/api/sakhi/stt?lang=..  -> Metro proxy (app/metro.config.js)
-> scripts/sakhi_gateway_dev.py :8787 -> stt_service :8795 (Gnani as fallback).

Auth: the app's dev Bearer token, read from app/.env.local into memory only
(never printed or written). Requests go one at a time, like one phone, paced
under the gateway's own STT limit (40 / min per client).

Writes scripts/stt_eval/results.json: per file the reference, the transcript,
provider, server ms, client ms, WER / CER, and errors.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from testset import CASES, STRESS  # noqa: E402

PACE_S = 1.6  # 40 / min, the gateway's per-client STT limit


def token() -> str:
    for line in (ROOT / "app" / ".env.local").read_text(encoding="utf-8").splitlines():
        if line.startswith("EXPO_PUBLIC_SAKHI_GATEWAY_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("no EXPO_PUBLIC_SAKHI_GATEWAY_TOKEN in app/.env.local")


def base_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1].rstrip("/")
    return (ROOT / "app" / "tunnel-url.txt").read_text(encoding="utf-8").strip().replace("exp://", "https://").rstrip("/")


def norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFC", text).lower()
    t = re.sub(r"[^\w\sऀ-ॿಀ-೿]", " ", t)  # drop punctuation, keep Indic marks
    t = t.replace("।", " ")  # danda
    return t.split()


def edits(a: list, b: list) -> int:
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
    return d[-1]


def score(ref: str, hyp: str) -> dict:
    r, h = norm(ref), norm(hyp)
    rc, hc = list("".join(r)), list("".join(h))
    return {"wer": edits(r, h) / max(len(r), 1), "cer": edits(rc, hc) / max(len(rc), 1), "exact": r == h}


def main() -> None:
    base, auth = base_url(), token()
    print(f"target: {base}/api/sakhi/stt")
    files = []
    stress = set(STRESS)
    for c in CASES:
        for variant in [""] + (["_fast", "_noisy"] if c["id"] in stress else []):
            files.append((c, variant or "clean", HERE / "audio" / f"{c['id']}{variant}.wav"))
    out, last = [], 0.0
    for n, (c, variant, path) in enumerate(files, 1):
        wait = PACE_S - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        body = path.read_bytes()
        req = urllib.request.Request(f"{base}/api/sakhi/stt?lang={c['lang']}", data=body, method="POST", headers={"Authorization": f"Bearer {auth}", "Content-Type": "audio/wav", "User-Agent": "sakhi-stt-eval/1.0"})
        row = {**c, "variant": variant, "audio_s": round((len(body) - 44) / 32000, 2)}
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            row.update(stt=data.get("text", ""), provider=data.get("provider"), server_ms=data.get("ms"), lid=data.get("lid_confidence"), status=200)
        except urllib.error.HTTPError as exc:
            row.update(stt="", provider=None, status=exc.code, error=exc.read()[:200].decode("utf-8", "replace"))
        except Exception as exc:  # timeout, network
            row.update(stt="", provider=None, status=0, error=str(exc)[:200])
        row["client_ms"] = round((time.perf_counter() - t) * 1000)
        row.update(score(c["text"], row["stt"]))
        out.append(row)
        print(f"{n:3d}/{len(files)} {row['id']:<22} {variant:<6} {row['status']} {row.get('provider')!s:<6} {row['client_ms']:>5} ms  wer {row['wer']:.2f}")
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = [r for r in out if r["status"] == 200]
    lat = sorted(r["client_ms"] for r in ok)
    if lat:
        print(f"\n{len(ok)}/{len(out)} ok · client p50 {statistics.median(lat):.0f} ms · p95 {lat[int(0.95 * (len(lat) - 1))]:.0f} ms")


if __name__ == "__main__":
    main()
