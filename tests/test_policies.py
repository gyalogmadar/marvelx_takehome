from claims_pipeline.policies import POLICIES


def test_every_policy_describes_itself_and_formulas_use_declared_facts():
    for policy in POLICIES.values():
        d = policy.describe()
        numeric = {f.name for f in policy.facts if f.type in ("money", "int")}
        for cov in policy.coverages:
            if cov.payout is not None:
                assert set(cov.payout.names()) <= numeric, (policy.name, cov.name)
            assert cov.proofs.doc_types() <= set(policy.doc_types), (policy.name, cov.name)
        ids = [e.id for e in policy.exclusions] + [c.matches.id for c in policy.coverages] + \
              [k.id for c in policy.coverages for k in c.checks]
        assert len(ids) == len(set(ids)), f"duplicate check ids in {policy.name}"
        assert d["coverages"] and d["exclusions"]
