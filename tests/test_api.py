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
    settings = Settings(backend="scripted", data_dir=str(tmp_path / "data"))
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
    claim_dir = ROOT / "12-missed-connection-formula"
    meta = json.loads((claim_dir / "claim.json").read_text())
    r = client.post("/claims", data={"description": (claim_dir / "description.txt").read_text(), "policy": "cfsr",
                                     "submitted_at": meta["submitted_at"], "wait": "true"}, files=upload_files(claim_dir))
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "done" and rec["result"]["decision"]["outcome"] == "APPROVE"
    assert rec["result"]["decision"]["payout"] == 200.0
    assert "oracle.json" not in rec["files"]           # the oracle is never evidence

    listing = client.get("/claims").json()
    assert listing[0]["claim_id"] == rec["claim_id"] and listing[0]["decision"] == "APPROVE"

    full = client.get(f"/claims/{rec['claim_id']}").json()
    doc = next(d for d in full["result"]["documents"] if d["doc_type"] == "booking_confirmation")
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
