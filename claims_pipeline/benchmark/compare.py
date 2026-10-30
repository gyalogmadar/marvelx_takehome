"""Compare benchmark runs claim by claim: two or more strategies over the same claims.

    python -m claims_pipeline.benchmark.compare results/decomposed results/e2e results/clef
    python -m claims_pipeline.benchmark.compare results/decomposed results/e2e --out results/compare.md
    python -m claims_pipeline.benchmark.compare 3 9           # recorded batches (GET /benchmarks lists their ids)

"Right" means the decision and, where one is expected, the payout; "decisions right" counts the
decision alone, so a strategy that computes no payout is not marked down twice.

The report puts the headline numbers side by side, one column per run, then
the whole claim-by-claim matrix with what each claim tests, then the
summaries of every claim on which the runs differ.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(run: str | Path) -> dict:
    """A run's results: a recorded batch by its id, or a results folder."""
    if str(run).isdigit():
        from claims_pipeline.settings import Settings
        from claims_pipeline.store.db import sessions
        from claims_pipeline.benchmark.views import batch_results

        with sessions(Settings().database_url)() as s:
            data = batch_results(s, int(run))
        if data is None:
            raise SystemExit(f"no batch {run}")
        data["name"] = f"batch {run} ({data['folder']})"
        return data
    return json.loads((Path(run) / "results.json").read_text())


def label(run: dict) -> str:
    """The run's column header: its strategy, plus the judge and scorer when they were swapped in."""
    strategy = run.get("strategy", "decomposed")
    if strategy != "decomposed":
        return strategy
    return (strategy + (f" + {run['judge']} judge" if run.get("judge") else "")
            + (f" + {run['scorer']} scores" if run.get("scorer") else ""))


def summary(run: dict) -> dict:
    m = run["metrics"]
    latency = round(sum(c["latency_ms"] for c in m["components"].values()) / max(m["claims"], 1))
    return {"name": run.get("name"), "title": run.get("title"), "label": label(run),
            "strategy": run.get("strategy", "decomposed"), "accuracy": m["accuracy"],
            "correct": m["correct"], "scored": m["scored"], "payout_exact": m["payout"]["exact"],
            "decisions_right": sum(1 for r in run["claims"] if r.get("expected") and r.get("actual") == r["expected"]),
            "reviews": m.get("reviews"),
            "payout_scored": m["payout"]["scored"], "cost": m["total_cost_usd"], "latency_ms_per_claim": latency,
            "confusion": m["confusion"], "per_outcome": m["per_outcome"]}


def compare(*runs: dict) -> dict:
    if len(runs) < 2:
        raise ValueError("compare needs at least two runs")
    rows = [{r["claim_id"]: r for r in run["claims"]} for run in runs]
    ids = sorted(set().union(*rows))
    claims = []
    for cid in ids:
        known = [rs[cid] for rs in rows if cid in rs]
        claims.append({
            "claim_id": cid,
            "note": next((r.get("note") for r in known if r.get("note")), None),
            "expected": next((r.get("expected") for r in known if r.get("expected")), None),
            "expected_payout": next((r.get("expected_payout") for r in known if r.get("expected_payout") is not None), None),
            "in_all": len(known) == len(rows),
            "results": [{"present": cid in rs, "actual": rs.get(cid, {}).get("actual"), "payout": rs.get(cid, {}).get("actual_payout"),
                         "ok": bool(rs.get(cid, {}).get("match")),
                         "summary": rs.get(cid, {}).get("summary") or rs.get(cid, {}).get("error")} for rs in rows],
        })
    shared = [c for c in claims if c["in_all"]]   # agreement only over claims every run processed
    scored = [c for c in shared if c["expected"]]
    oks = lambda c: [r["ok"] for r in c["results"]]
    agreement = {
        "claims": len(shared),
        "missing": len(claims) - len(shared),
        "all_right": sum(all(oks(c)) for c in scored),
        "none_right": sum(not any(oks(c)) for c in scored),
        "same_decision": sum(len({r["actual"] for r in c["results"]}) == 1 for c in shared),
        "only_right": [sum(c["results"][i]["ok"] and sum(oks(c)) == 1 for c in scored) for i in range(len(runs))],
    }
    return {"runs": [summary(r) for r in runs], "agreement": agreement, "claims": claims}


def markdown(c: dict) -> str:
    runs, g = c["runs"], c["agreement"]
    pct = lambda v: "n/a" if v is None else f"{v:.1%}"
    money = lambda v: "–" if v is None else f"{v:,.2f}"
    cols = lambda cells: "| " + " | ".join(str(x) for x in cells) + " |"
    head = [f"{chr(65 + i)}: {r['label']}" for i, r in enumerate(runs)]
    out = [" vs ".join(r["title"] or r["name"] or "?" for r in runs), "", cols(["", *head]), cols(["---"] * (len(runs) + 1)),
           cols(["run", *(r["name"] for r in runs)]),
           cols(["right (decision and payout)", *(f"{pct(r['accuracy'])} ({r['correct']}/{r['scored']})" for r in runs)]),
           cols(["decisions right", *(f"{r['decisions_right']}/{r['scored']}" for r in runs)]),
           cols(["payouts exact", *(f"{r['payout_exact']}/{r['payout_scored']}" for r in runs)]),
           cols(["sent to a person by a review flag (of which expected a referral)",
                 *("–" if not r["reviews"] else f"{r['reviews']['claims']} ({r['reviews']['expected_uncertain']})" for r in runs)]),
           cols(["model cost (USD)", *(f"{r['cost']:.4f}" for r in runs)]),
           cols(["model latency per claim (ms)", *(r["latency_ms_per_claim"] for r in runs)])]
    for o in ("APPROVE", "DENY", "UNCERTAIN"):
        out.append(cols([f"{o} precision / recall",
                         *(f"{r['per_outcome'].get(o, {}).get('precision')} / {r['per_outcome'].get(o, {}).get('recall')}" for r in runs)]))
    out[0] = "# " + out[0]
    out += ["", "## Agreement", "",
            f"- all right: {g['all_right']}", f"- none right: {g['none_right']}",
            *(f"- only {chr(65 + i)} right: {n}" for i, n in enumerate(g["only_right"])),
            f"- same decision: {g['same_decision']} of {g['claims']}",
            *([f"- not in every run (left out above): {g['missing']}"] if g["missing"] else []), "",
            "## Every claim", "", "✓ = matches the expected decision (and payout, when one is expected).", "",
            cols(["claim", "expected", *(chr(65 + i) for i in range(len(runs)))]), cols(["---"] * (len(runs) + 2))]

    def cell(r: dict) -> str:
        if not r["present"]:
            return "–"
        if not r["actual"]:
            return "error"
        return r["actual"] + (f" {money(r['payout'])}" if r["payout"] is not None else "") + (" ✓" if r["ok"] else "")

    for x in c["claims"]:
        expected = (x["expected"] or "-") + (f" {money(x['expected_payout'])}" if x["expected_payout"] is not None else "")
        out.append(cols([x["claim_id"], expected, *(cell(r) for r in x["results"])]))
    diffs = [x for x in c["claims"] if x["in_all"] and len({(r["actual"], r["payout"]) for r in x["results"]}) > 1]
    out += ["", "## Where the runs differ", ""]
    if not diffs:
        out.append("(none)")
    for x in diffs:
        out += [f"**{x['claim_id']}** (expected {x['expected']})" + (f": {x['note']}" if x["note"] else ""), ""]
        out += [f"- {chr(65 + i)}: {r['summary']}" for i, r in enumerate(x["results"])] + [""]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="two or more benchmark output folders or batch ids")
    ap.add_argument("--out", type=Path, default=None, help="markdown path (default: <last run>/compare-<others>.md)")
    args = ap.parse_args(argv)
    if len(args.runs) < 2:
        ap.error("give at least two runs")
    c = compare(*(load(r) for r in args.runs))
    names = [r if r.isdigit() else Path(r).name for r in args.runs]
    out = args.out or (Path("results") / ("compare-" + "-".join(names) + ".md") if args.runs[-1].isdigit()
                       else Path(args.runs[-1]) / ("compare-" + "-".join(names[:-1]) + ".md"))
    out.write_text(markdown(c))
    out.with_suffix(".json").write_text(json.dumps(c, indent=2))
    accuracy = ", ".join(f"{r['label']} {r['accuracy']}" for r in c["runs"])
    print(f"{accuracy}; same decision on {c['agreement']['same_decision']}/{c['agreement']['claims']}; report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
