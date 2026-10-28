"""Compare two benchmark runs claim by claim.

    python -m claims_pipeline.benchmark.compare results/decomposed results/e2e --out results/compare.md

Typically run A is the decomposed pipeline and run B the end-to-end single
call over the same claims.  The report puts the headline numbers side by
side, then lists every claim where the two disagree, with both summaries.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(run_dir: Path) -> dict:
    return json.loads((Path(run_dir) / "results.json").read_text())


def compare(a: dict, b: dict) -> dict:
    rows_a = {r["claim_id"]: r for r in a["claims"]}
    rows_b = {r["claim_id"]: r for r in b["claims"]}
    ids = sorted(set(rows_a) | set(rows_b))
    per_claim = []
    for cid in ids:
        ra, rb = rows_a.get(cid, {}), rows_b.get(cid, {})
        per_claim.append({
            "claim_id": cid,
            "expected": ra.get("expected") or rb.get("expected"),
            "expected_payout": ra.get("expected_payout", rb.get("expected_payout")),
            "a": ra.get("actual"), "a_payout": ra.get("actual_payout"), "a_ok": ra.get("match"), "a_summary": ra.get("summary") or ra.get("error"),
            "b": rb.get("actual"), "b_payout": rb.get("actual_payout"), "b_ok": rb.get("match"), "b_summary": rb.get("summary") or rb.get("error"),
        })
    both = [c for c in per_claim if c["a_ok"] and c["b_ok"]]
    only_a = [c for c in per_claim if c["a_ok"] and not c["b_ok"]]
    only_b = [c for c in per_claim if c["b_ok"] and not c["a_ok"]]
    neither = [c for c in per_claim if c["expected"] and not c["a_ok"] and not c["b_ok"]]
    ma, mb = a["metrics"], b["metrics"]

    def lat(m):
        comps = m["components"].values()
        calls = sum(c["calls"] for c in comps)
        return round(sum(c["latency_ms"] for c in comps) / max(m["claims"], 1))

    return {
        "a": {"name": a.get("name"), "title": a.get("title"), "strategy": a.get("strategy", "decomposed"), "accuracy": ma["accuracy"],
              "correct": ma["correct"], "scored": ma["scored"], "payout_exact": ma["payout"]["exact"], "payout_scored": ma["payout"]["scored"],
              "cost": ma["total_cost_usd"], "latency_ms_per_claim": lat(ma), "confusion": ma["confusion"], "per_outcome": ma["per_outcome"]},
        "b": {"name": b.get("name"), "title": b.get("title"), "strategy": b.get("strategy", "decomposed"), "accuracy": mb["accuracy"],
              "correct": mb["correct"], "scored": mb["scored"], "payout_exact": mb["payout"]["exact"], "payout_scored": mb["payout"]["scored"],
              "cost": mb["total_cost_usd"], "latency_ms_per_claim": lat(mb), "confusion": mb["confusion"], "per_outcome": mb["per_outcome"]},
        "agreement": {"both_right": len(both), "only_a_right": len(only_a), "only_b_right": len(only_b), "neither": len(neither),
                      "same_decision": len([c for c in per_claim if c["a"] == c["b"]]), "claims": len(per_claim)},
        "claims": per_claim,
    }


def markdown(c: dict) -> str:
    a, b, g = c["a"], c["b"], c["agreement"]
    pct = lambda v: "n/a" if v is None else f"{v:.1%}"
    money = lambda v: "–" if v is None else f"{v:,.2f}"
    out = [f"# {a['title']} vs {b['title']}", "",
           "| | A: " + a["strategy"] + " | B: " + b["strategy"] + " |", "|---|---|---|",
           f"| decision accuracy | {pct(a['accuracy'])} ({a['correct']}/{a['scored']}) | {pct(b['accuracy'])} ({b['correct']}/{b['scored']}) |",
           f"| payouts exact | {a['payout_exact']}/{a['payout_scored']} | {b['payout_exact']}/{b['payout_scored']} |",
           f"| model cost (USD) | {a['cost']:.4f} | {b['cost']:.4f} |",
           f"| model latency per claim (ms) | {a['latency_ms_per_claim']} | {b['latency_ms_per_claim']} |", ""]
    for o in ("APPROVE", "DENY", "UNCERTAIN"):
        pa, pb = a["per_outcome"].get(o, {}), b["per_outcome"].get(o, {})
        out.append(f"| {o} precision / recall | {pa.get('precision')} / {pa.get('recall')} | {pb.get('precision')} / {pb.get('recall')} |")
    out += ["", "## Agreement", "",
            f"- both right: {g['both_right']}", f"- only A right: {g['only_a_right']}", f"- only B right: {g['only_b_right']}",
            f"- neither right: {g['neither']}", f"- same decision: {g['same_decision']} of {g['claims']}", "",
            "## Claims where the two differ", "",
            "| claim | expected | A | B | A payout | B payout |", "|---|---|---|---|---|---|"]
    diffs = [x for x in c["claims"] if x["a"] != x["b"] or (x["a_payout"] != x["b_payout"])]
    for x in diffs:
        out.append(f"| {x['claim_id']} | {x['expected'] or '-'} | {x['a'] or '-'}{' ✓' if x['a_ok'] else ''} | {x['b'] or '-'}{' ✓' if x['b_ok'] else ''} | {money(x['a_payout'])} | {money(x['b_payout'])} |")
    if not diffs:
        out.append("| (none) | | | | | |")
    out += ["", "### Reasoning side by side", ""]
    for x in diffs:
        out += [f"**{x['claim_id']}** (expected {x['expected']})", "", f"- A: {x['a_summary']}", f"- B: {x['b_summary']}", ""]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_a", type=Path)
    ap.add_argument("run_b", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="markdown path (default: <run_b>/compare-<run_a>.md)")
    args = ap.parse_args(argv)
    c = compare(load(args.run_a), load(args.run_b))
    out = args.out or (args.run_b / f"compare-{args.run_a.name}.md")
    out.write_text(markdown(c))
    (out.with_suffix(".json")).write_text(json.dumps(c, indent=2))
    g = c["agreement"]
    print(f"A {c['a']['accuracy']} vs B {c['b']['accuracy']}; same decision on {g['same_decision']}/{g['claims']}; report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
