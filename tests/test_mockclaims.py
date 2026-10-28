"""Every mock claim must resolve to its expected decision and payout, offline."""
from pathlib import Path

import pytest

from claims_pipeline.benchmark.loader import claim_dirs, load_claim_dir
from claims_pipeline.components.explain import evidence_outline
from claims_pipeline.llm.scripted import ScriptedBackend
from claims_pipeline.pipeline import process_claim
from claims_pipeline.policies import get_policy

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"


@pytest.mark.parametrize("claim_dir", claim_dirs(ROOT), ids=lambda p: p.name)
def test_mock_claim(claim_dir: Path):
    loaded = load_claim_dir(claim_dir)
    policy = get_policy(loaded.policy)
    result = process_claim(loaded.input, policy, ScriptedBackend.from_file(loaded.oracle_path))

    assert result.decision.outcome.value == loaded.expected["decision"], result.decision.summary
    if loaded.expected["payout"] is not None:
        assert result.decision.payout == pytest.approx(loaded.expected["payout"])

    # the tree is internally consistent: every reference resolves
    doc_ids = {d.id for d in result.documents}
    fact_names = {f.name for f in result.facts}
    for r in result.exclusions + [x for c in result.coverages for x in (c.match, c.proofs, *c.checks)]:
        assert set(r.document_ids) <= doc_ids, r.id
        assert set(r.facts_used) <= fact_names, r.id
    for f in result.facts:
        for o in f.observations:
            assert o.document_id in doc_ids
            doc = result.document(o.document_id)
            if o.line is not None:
                assert doc.quote(o.line) == o.quote, (f.name, o)
    all_check_ids = {r.id for r in result.exclusions} | {x.id for c in result.coverages for x in (c.match, c.proofs, *c.checks)}
    assert set(result.decision.determining) <= all_check_ids
    assert evidence_outline(result)
    assert all(call.ok for call in result.calls)
