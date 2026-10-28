"""Load a claim folder into pipeline input.

A folder holds description.txt plus the submitted documents.  Optional
sidecars: claim.json (policy, submission date, note), expected.json or
answer.json (the expected outcome), oracle.json (scripted model answers).
The sidecars are never passed to the pipeline as documents.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from claims_pipeline.pipeline import ClaimFile, ClaimInput

SIDECARS = {"description.txt", "answer.json", "expected.json", "oracle.json", "claim.json", ".DS_Store"}


@dataclass
class LoadedClaim:
    input: ClaimInput
    policy: str | None
    expected: dict | None
    oracle_path: Path | None
    note: str = ""


def normalize_expected(raw: dict) -> dict:
    """Pull decision and payout out of whichever keys the answer file uses."""
    decision = None
    for key in ("decision", "outcome", "expected_decision", "status", "result"):
        if isinstance(raw.get(key), str):
            decision = raw[key].strip().upper()
            break
    payout = None
    for key in ("payout", "amount", "compensation", "expected_payout", "compensation_amount", "payout_amount"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            payout = float(value)
            break
        if isinstance(value, str):
            digits = "".join(ch for ch in value if ch.isdigit() or ch in ".,")
            try:
                payout = float(digits.replace(",", "")) if digits else None
                break
            except ValueError:
                pass
    return {"decision": decision, "payout": payout, "raw": raw}


def load_claim_dir(path: Path) -> LoadedClaim:
    path = Path(path)
    description = (path / "description.txt").read_text().strip()
    meta = json.loads((path / "claim.json").read_text()) if (path / "claim.json").exists() else {}
    submitted = meta.get("submitted_at")
    submitted_at = datetime.fromisoformat(submitted).replace(tzinfo=timezone.utc) if submitted else None
    files = [ClaimFile(filename=p.name, content=p.read_bytes())
             for p in sorted(path.iterdir()) if p.is_file() and p.name not in SIDECARS and not p.name.startswith(".")]
    expected = None
    for name in ("expected.json", "answer.json"):
        if (path / name).exists():
            expected = normalize_expected(json.loads((path / name).read_text()))
            break
    oracle = path / "oracle.json"
    return LoadedClaim(
        input=ClaimInput(claim_id=path.name, description=description, files=files,
                         metadata={k: str(v) for k, v in meta.get("metadata", {}).items()}, submitted_at=submitted_at),
        policy=meta.get("policy"),
        expected=expected,
        oracle_path=oracle if oracle.exists() else None,
        note=meta.get("note", ""),
    )


def claim_dirs(root: Path) -> list[Path]:
    return sorted(p for p in Path(root).iterdir() if p.is_dir() and (p / "description.txt").exists())
