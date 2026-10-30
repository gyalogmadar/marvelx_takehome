"""Metrics and the markdown report for a benchmark run."""
from __future__ import annotations

from collections import Counter, defaultdict

from claims_pipeline.components.explain import evidence_outline
from claims_pipeline.domain import ClaimResult
from claims_pipeline.llm.pricing import cost_usd

OUTCOMES = ("APPROVE", "DENY", "UNCERTAIN")


def score_row(row: dict, result: ClaimResult) -> dict:
    """Fill a benchmark row from a result: right when the decision matches and, where a payout
    is expected, the payout matches to the cent."""
    row.update(actual=result.decision.outcome.value, actual_payout=result.decision.payout,
               confidence=result.decision.confidence, summary=result.decision.summary,
               failed_calls=sum(not c.ok for c in result.calls))
    row["match"] = bool(row["expected"]) and row["expected"] == row["actual"] and (
        row["expected_payout"] is None or (row["actual_payout"] is not None
                                           and abs(row["actual_payout"] - row["expected_payout"]) < 0.01))
    return row


def metrics(rows: list[dict], results: dict[str, ClaimResult]) -> dict:
    scored = [r for r in rows if r["expected"]]
    correct = [r for r in scored if r["match"]]
    confusion = {e: {a: 0 for a in OUTCOMES} for e in OUTCOMES}
    for r in scored:
        if r["expected"] in confusion:
            confusion[r["expected"]][r["actual"]] += 1
    payout_rows = [r for r in scored if r["expected_payout"] is not None]
    payout_ok = [r for r in payout_rows if r["actual_payout"] is not None and abs(r["actual_payout"] - r["expected_payout"]) < 0.01]
    per_outcome = {}
    for o in OUTCOMES:
        exp = [r for r in scored if r["expected"] == o]
        act = [r for r in scored if r["actual"] == o]
        tp = len([r for r in exp if r["match"]])
        per_outcome[o] = {"expected": len(exp), "predicted": len(act),
                          "precision": round(tp / len(act), 3) if act else None,
                          "recall": round(tp / len(exp), 3) if exp else None}
    components: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                       "cache_read_tokens": 0, "cost_usd": 0.0, "latency_ms": 0, "errors": 0})
    for res in results.values():
        for call in res.calls:
            c = components[call.component]
            c["calls"] += 1
            c["input_tokens"] += call.input_tokens
            c["output_tokens"] += call.output_tokens
            c["cache_read_tokens"] += call.cache_read_tokens
            # priced from the tokens with today's table (llm/pricing.py), so older runs are priced too
            c["cost_usd"] = round(c["cost_usd"] + cost_usd(call.model, call.input_tokens, call.output_tokens,
                                                           call.cache_read_tokens), 6)
            c["latency_ms"] += call.latency_ms
            c["errors"] += 0 if call.ok else 1
    # the review signal: how often a component sent a claim to a person, and whether it should have gone
    flagged = [r for r in rows if r["claim_id"] in results and results[r["claim_id"]].decision.reviews]
    reviews = {"claims": len(flagged), "rate": round(len(flagged) / len(rows), 3) if rows else None,
               "expected_uncertain": sum(r["expected"] == "UNCERTAIN" for r in flagged),
               "expected_other": sum(r["expected"] not in (None, "UNCERTAIN") for r in flagged)}
    return {
        "claims": len(rows),
        "reviews": reviews,
        "scored": len(scored),
        "correct": len(correct),
        "accuracy": round(len(correct) / len(scored), 4) if scored else None,
        "confusion": confusion,
        "per_outcome": per_outcome,
        "payout": {"scored": len(payout_rows), "exact": len(payout_ok)},
        "components": dict(components),
        "total_cost_usd": round(sum(c["cost_usd"] for c in components.values()), 4),
        "unprocessed": len([r for r in rows if r.get("error")]),
    }


def markdown(rows: list[dict], results: dict[str, ClaimResult], m: dict, title: str) -> str:
    out = [f"# {title}", ""]
    acc = f"{m['accuracy']:.1%}" if m["accuracy"] is not None else "n/a"
    out += [f"**Decisions:** {m['correct']} / {m['scored']} correct ({acc}).  "
            f"**Payouts:** {m['payout']['exact']} / {m['payout']['scored']} exact.  "
            f"**Model cost:** ${m['total_cost_usd']:.4f}.  "
            f"**Sent to a person by a review flag:** {m['reviews']['claims']} "
            f"({m['reviews']['expected_uncertain']} expected a referral, {m['reviews']['expected_other']} did not).", ""]
    out += ["## Confusion matrix (rows: expected, columns: actual)", "",
            "| expected \\ actual | " + " | ".join(OUTCOMES) + " |", "|---|" + "---|" * len(OUTCOMES)]
    for e in OUTCOMES:
        out.append(f"| {e} | " + " | ".join(str(m["confusion"][e][a]) for a in OUTCOMES) + " |")
    out += ["", "## Per outcome", "", "| outcome | expected | predicted | precision | recall |", "|---|---|---|---|---|"]
    for o, v in m["per_outcome"].items():
        out.append(f"| {o} | {v['expected']} | {v['predicted']} | {v['precision'] if v['precision'] is not None else '-'} | {v['recall'] if v['recall'] is not None else '-'} |")
    out += ["", "## Model calls per component", "",
            "| component | calls | input tok | cache read | output tok | cost USD | latency ms | errors |", "|---|---|---|---|---|---|---|---|"]
    for name, c in m["components"].items():
        out.append(f"| {name} | {c['calls']} | {c['input_tokens']} | {c['cache_read_tokens']} | {c['output_tokens']} | {c['cost_usd']:.4f} | {c['latency_ms']} | {c['errors']} |")
    out += ["", "## Claims", "", "| claim | expected | actual | ok | payout exp | payout act | confidence | summary |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ok = "yes" if r["match"] else ("-" if not r["expected"] else "**no**")
        summary = (r["summary"] or "").replace("|", "/")
        if len(summary) > 140:
            summary = summary[:137] + "..."
        out.append(f"| {r['claim_id']} | {r['expected'] or '-'} | {r['actual'] or '-'} | {ok} | "
                   f"{_money(r['expected_payout'])} | {_money(r['actual_payout'])} | {r['confidence']:.2f} | {summary} |"
                   if r["confidence"] is not None else
                   f"| {r['claim_id']} | {r['expected'] or '-'} | error | - | {_money(r['expected_payout'])} | - | - | {r.get('error', '')} |")
    misses = [r for r in rows if r["expected"] and not r["match"]]
    if misses:
        out += ["", "## Mismatches with evidence", ""]
        for r in misses:
            out += [f"### {r['claim_id']}: expected {r['expected']}, got {r['actual']}", ""]
            if r.get("note"):
                out += [f"_{r['note']}_", ""]
            res = results.get(r["claim_id"])
            if res is not None:
                out += ["```", *evidence_outline(res), "```", ""]
    return "\n".join(out) + "\n"


def _money(v: float | None) -> str:
    return "-" if v is None else f"{v:,.2f}"
