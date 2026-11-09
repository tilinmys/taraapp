"""Summarise results.json (+ nlu_compare.json, agent_compare.json) into REPORT.md."""

from __future__ import annotations

import collections
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent


def pct(n, d):
    return f"{100 * n / d:.0f}%" if d else "-"


def p(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else 0


def main() -> None:
    rows = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    nlu = {(r["id"], r["variant"]): r for r in json.loads((HERE / "nlu_compare.json").read_text(encoding="utf-8"))} if (HERE / "nlu_compare.json").exists() else {}
    ok = [r for r in rows if r["status"] == 200]
    L = ["# STT end-to-end report (phone path through the tunnel)", ""]
    L.append(f"{len(rows)} requests, {len(ok)} answered, providers: {dict(collections.Counter(r.get('provider') for r in ok))}")
    L.append("")
    L.append("## Accuracy by language and variant")
    L.append("| lang | variant | n | exact | mean WER | mean CER | NLU record same |")
    L.append("|---|---|---|---|---|---|---|")
    for lang in ("kn", "hi", "en"):
        for var in ("clean", "fast", "noisy"):
            g = [r for r in ok if r["lang"] == lang and r["variant"] == var]
            if not g:
                continue
            agree = [nlu[(r["id"], var)]["agree"] for r in g if (r["id"], var) in nlu]
            L.append(f"| {lang} | {var} | {len(g)} | {pct(sum(r['exact'] for r in g), len(g))} | {statistics.mean(r['wer'] for r in g):.2f} | {statistics.mean(r['cer'] for r in g):.2f} | {pct(sum(agree), len(agree))} |")
    L.append("")
    L.append("## Latency (client side, phone path)")
    L.append("| set | n | p50 ms | p95 ms | max ms | server p50 ms |")
    L.append("|---|---|---|---|---|---|")
    for name, g in [("all", ok)] + [(f"lang {x}", [r for r in ok if r["lang"] == x]) for x in ("kn", "hi", "en")]:
        L.append(f"| {name} | {len(g)} | {p([r['client_ms'] for r in g], .5)} | {p([r['client_ms'] for r in g], .95)} | {max(r['client_ms'] for r in g)} | {p([r['server_ms'] or 0 for r in g], .5)} |")
    L.append("")
    L.append("## Per question (clean speech): exact / NLU record same")
    by_q = collections.defaultdict(list)
    for r in ok:
        if r["variant"] == "clean":
            by_q[r["qid"]].append(r)
    L.append("| question | n | exact | NLU same | misses (said -> heard) |")
    L.append("|---|---|---|---|---|")
    for q, g in by_q.items():
        agree = [nlu[(r["id"], "clean")]["agree"] for r in g if (r["id"], "clean") in nlu]
        miss = "; ".join(f"{r['lang']}: {r['text']} -> {r['stt']}" for r in g if not r["exact"])
        L.append(f"| {q} | {len(g)} | {pct(sum(r['exact'] for r in g), len(g))} | {pct(sum(agree), len(agree))} | {miss} |")
    fails = [r for r in rows if r["status"] != 200]
    L.append("")
    L.append(f"## Failures ({len(fails)})")
    for r in fails:
        L.append(f"- {r['id']} {r['variant']}: HTTP {r['status']} {r.get('error', '')}")
    if nlu:
        L.append("")
        L.append("## NLU: record differs (STT error) / NLU gaps (reference itself not understood)")
        for o in nlu.values():
            if not o["agree"]:
                L.append(f"- STT {o['id']} {o['variant']}: said `{o['said']}` heard `{o['heard']}` -> ref {o['ref']} / stt {o['stt']}")
        for o in nlu.values():
            if o["nluGap"] and o["variant"] == "clean":
                L.append(f"- NLU gap {o['id']}: `{o['said']}` -> {o['ref']}")
    (HERE / "REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:40]))


if __name__ == "__main__":
    main()
