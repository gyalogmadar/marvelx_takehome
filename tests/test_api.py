import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claims_pipeline.api import create_app
from claims_pipeline.llm import Settings

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"


@pytest.fixture
def client(tmp_path):
    settings = Settings(backend="scripted", database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")
    with TestClient(create_app(settings)) as c:
        yield c


def upload_files(claim_dir: Path):
    files = []
    for p in sorted(claim_dir.iterdir()):
        if p.name in ("description.txt", "expected.json", "claim.json") or p.name.startswith("."):
            continue
        files.append(("files", (p.name, p.read_bytes())))
    return files


def test_submit_wait_and_fetch(client):
    claim_dir = ROOT / "14-jury-duty"
    meta = json.loads((claim_dir / "claim.json").read_text())
    r = client.post("/claims", data={"description": (claim_dir / "description.txt").read_text(), "policy": "cfsr",
                                     "submitted_at": meta["submitted_at"], "wait": "true"}, files=upload_files(claim_dir))
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "done" and rec["result"]["decision"]["outcome"] == "APPROVE"
    assert rec["result"]["decision"]["payout"] == 560.0
    assert "oracle.json" not in rec["files"]           # the oracle is never evidence

    listing = client.get("/claims").json()
    assert listing[0]["claim_id"] == rec["claim_id"] and listing[0]["decision"] == "APPROVE"

    full = client.get(f"/claims/{rec['claim_id']}").json()
    doc = next(d for d in full["result"]["readings"] if d["doc_type"] == "booking_confirmation")
    raw = client.get(f"/claims/{rec['claim_id']}/documents/{doc['id']}")
    assert raw.status_code == 200 and "Booking confirmation" in raw.text
    assert client.get(f"/claims/{rec['claim_id']}/documents/description").status_code == 404


def test_background_processing_and_policies(client):
    claim_dir = ROOT / "g01-theft-clean"
    r = client.post("/claims", data={"description": "x", "policy": "gadget", "submitted_at": "2025-06-11"},
                    files=upload_files(claim_dir))
    assert r.status_code == 202
    claim_id = r.json()["claim_id"]
    for _ in range(50):
        rec = client.get(f"/claims/{claim_id}").json()
        if rec["status"] != "processing":
            break
        time.sleep(0.05)
    assert rec["status"] == "done", rec.get("error")
    assert client.get("/policies/cfsr").json()["coverages"][0]["name"] == "cancellation"
    assert client.get("/policies/nope").status_code == 404
    assert client.post("/claims", data={"description": "x", "policy": "nope"}).status_code == 400
    assert client.get("/health").json()["backend"] == "scripted"


def test_reprocessing_adds_a_run_and_keeps_the_first(client):
    claim_dir = ROOT / "01-clean-medical-cancellation"
    rec = client.post("/claims", data={"description": (claim_dir / "description.txt").read_text(), "wait": "true",
                                       "submitted_at": "2025-03-25"}, files=upload_files(claim_dir)).json()
    first = rec["run_id"]
    assert rec["result"]["decision"]["outcome"] == "APPROVE"
    again = client.post(f"/claims/{rec['claim_id']}/runs", params={"wait": "true"})
    assert again.status_code == 200 and again.json()["run_id"] != first
    runs = client.get(f"/claims/{rec['claim_id']}/runs").json()
    assert [r["run_id"] for r in runs] == [first, again.json()["run_id"]]
    assert all(r["config_id"] for r in runs)
    # the latest run is the default; the first is still there, unchanged
    assert client.get(f"/claims/{rec['claim_id']}").json()["run_id"] == again.json()["run_id"]
    kept = client.get(f"/claims/{rec['claim_id']}", params={"run": first}).json()
    assert kept["result"] == rec["result"]
    assert client.get(f"/claims/{rec['claim_id']}", params={"run": "nope"}).status_code == 404
    calls = client.get(f"/claims/{rec['claim_id']}/calls").json()
    assert calls and all(c["model"] == "scripted" and c["request"] is None for c in calls)  # oracle answers, no model


def test_benchmark_runs_are_served_from_the_store(client, tmp_path, monkeypatch):
    import shutil

    from claims_pipeline.benchmark import run as bench

    root = tmp_path / "mini"
    shutil.copytree(ROOT / "01-clean-medical-cancellation", root / "01-clean-medical-cancellation")
    monkeypatch.setenv("CLAIMS_DATABASE_URL", f"sqlite:///{tmp_path / 'pipeline.db'}")
    bench.run(root, tmp_path / "out", None, "scripted", True, "Mini benchmark")
    (summary,) = client.get("/benchmarks").json()
    assert summary["title"] == "Mini benchmark" and summary["correct"] == 1 and summary["folder"] == "out"
    data = client.get(f"/benchmarks/{summary['name']}").json()
    (row,) = data["claims"]
    assert row["match"] and row["expected"] == "APPROVE"
    rec = client.get(f"/benchmarks/{summary['name']}/claims/{row['claim_id']}").json()
    assert rec["expected"] == "APPROVE" and rec["result"]["decision"]["payout"] == 1240.0
    doc = next(d for d in rec["result"]["readings"] if d["kind"] == "markdown")
    assert client.get(f"/benchmarks/{summary['name']}/claims/{row['claim_id']}/documents/{doc['id']}").status_code == 200
    assert client.get("/benchmarks/not-a-run").status_code == 404
