"""The STT results through the phone agent's own algorithm.

    python scripts/stt_eval/agent_compare.py

For each transcribed answer (results.json), runs the reference text and the
STT transcript through:
  sakhi_slot_engine.extract_all   slots (age, numbers, readings, yes / no ...)
  sakhi_domain.detect_symptoms    danger symptoms (negation-aware)
  guardrails.classify_turn        emergency / advice / ... classification
and writes agent_compare.json: where the STT changes what the agent extracts,
and what each REFERENCE answer yields (to spot parsing bugs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

import guardrails  # noqa: E402
import sakhi_domain  # noqa: E402
import sakhi_slot_engine  # noqa: E402


def plain(x):
    """JSON-safe view of whatever the engine returns (dataclasses, sets, enums)."""
    if hasattr(x, "__dataclass_fields__"):
        return {k: plain(getattr(x, k)) for k in x.__dataclass_fields__}
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items() if v not in (None, [], {}, set(), "")}
    if isinstance(x, (set, frozenset)):
        return sorted(plain(v) for v in x)
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    if hasattr(x, "value") and not isinstance(x, (str, int, float)):
        return x.value
    return x


def run(text: str) -> dict:
    out = {}
    try:
        out["slots"] = plain(sakhi_slot_engine.extract_all(text))
    except Exception as exc:  # a crash is a finding
        out["slots_error"] = repr(exc)
    try:
        out["symptoms"] = plain(sakhi_domain.detect_symptoms(text))
    except Exception as exc:
        out["symptoms_error"] = repr(exc)
    try:
        out["turn"] = guardrails.classify_turn(text)
    except Exception as exc:
        out["turn_error"] = repr(exc)
    return out


def main() -> None:
    rows = [r for r in json.loads((HERE / "results.json").read_text(encoding="utf-8")) if r["status"] == 200]
    report, diff = [], 0
    for r in rows:
        ref, stt = run(r["text"]), run(r["stt"])
        changed = sorted(k for k in set(ref) | set(stt) if ref.get(k) != stt.get(k))
        diff += bool(changed)
        report.append({"id": r["id"], "variant": r["variant"], "lang": r["lang"], "qid": r["qid"], "said": r["text"], "heard": r["stt"], "ref": ref, "stt": stt, "changed": changed})
    (HERE / "agent_compare.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    errors = [x for x in report if any(k.endswith("_error") for k in x["ref"])]
    print(f"{len(rows)} answers · agent output changed by STT: {diff} · engine errors on reference text: {len(errors)}")


if __name__ == "__main__":
    main()
