"""A run is recorded completely and can be replayed: config, calls, prompts, result tree."""
import json
import shutil
from pathlib import Path

import httpx
import pytest

from claims_pipeline import prompts
from claims_pipeline.benchmark import run as bench
from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.e2e import process_claim_e2e
from claims_pipeline.llm.chat_backend import ChatBackend
from claims_pipeline.settings import Settings
from claims_pipeline.llm.openai_client import OpenAIClient
from claims_pipeline.llm.scripted import ScriptedBackend
from claims_pipeline.pipeline import process_claim
from claims_pipeline.policies import get_policy
from claims_pipeline.store import models as m
from claims_pipeline.store.db import sessions as open_sessions
from claims_pipeline.replay import replay_run
from claims_pipeline.store.repository import load_run
from claims_pipeline.store.runconfig import build_config, save_config
from claims_pipeline.store.runs import record_run

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"

REPLIES = {
    "ReadOut": {"doc_type": "booking_confirmation", "language": "en", "summary": "booking"},
    "ExtractOut": {"observations": [{"fact": "incident_date", "document_id": "description", "line": 1,
                                     "quote": "x", "value": "2025-03-04", "confidence": 0.9}]},
    "JudgmentOut": {"answer": "no", "rationale": "voluntary", "confidence": 0.9},
    "ExplainOut": {"text": "Dear Emma"},
    "E2EOut": {"outcome": "DENY", "confidence": 0.8, "summary": "Voluntary.", "reasoning": "Not covered."},
}


def local_model(request):
    name = json.loads(request.content)["response_format"]["json_schema"]["name"]
    return httpx.Response(200, json={"model": "qwen-test", "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                                     "choices": [{"finish_reason": "stop",
                                                  "message": {"content": json.dumps(REPLIES[name])}}]})


@pytest.fixture
def env(tmp_path):
    settings = Settings(backend="openai", openai_base_url="http://local.test/v1", trace_dir=str(tmp_path / "trace"))
    client = OpenAIClient(settings, http=httpx.Client(base_url="http://local.test/v1",
                                                      transport=httpx.MockTransport(local_model)))
    return settings, client, open_sessions(f"sqlite:///{tmp_path / 'pipeline.db'}")


def record(env, claim="10-voluntary-change", strategy="decomposed"):
    settings, client, sessions = env
    loaded = load_claim_dir(ROOT / claim)
    policy = get_policy(loaded.policy)
    process = (lambda: process_claim(loaded.input, policy, ChatBackend(settings, client))) if strategy == "decomposed" \
        else (lambda: process_claim_e2e(loaded.input, policy, client))
    return record_run(sessions, loaded.input, policy, settings, strategy=strategy, backend="openai", process=process)


def without_timing(result):
    data = result.model_dump(mode="json", exclude={"processed_at"})
    for call in data["calls"]:
        call.pop("latency_ms")
    return data


def test_a_run_is_recorded_with_its_config_calls_and_prompts(env):
    run_id, result = record(env)
    with env[2]() as s:
        run = s.get(m.Run, run_id)
        assert run.status == "done" and run.config_id and run.policy_id
        config = s.get(m.RunConfig, run.config_id).config
        assert config["run"]["strategy"] == "decomposed" and s.get(m.Policy, run.policy_id).name == "cfsr"
        assert config["prompts"] == {n: p.id for n, p in prompts.all_prompts().items()}
        assert "openai_api_key" not in config["settings"] and "database_url" not in config["settings"]
        # every call of the run is in the database, complete, and names its prompt versions
        calls = run.calls
        assert [c.call_id for c in calls] == [c.call_id for c in result.calls]
        assert all(c.request and c.parsed and c.response for c in calls)
        assert all({"role", "system", c.component} <= {p["name"] for p in c.prompts} for c in calls)
        assert {p["id"] for c in calls for p in c.prompts} <= {p.id for p in s.query(m.Prompt)}
        # the proof chain points at those calls
        judged = [c for c in run.checks if c.kind == "judged"]
        assert judged and all(s.query(m.ModelCall).filter_by(call_id=c.call_id).one().component == "judge" for c in judged)
        assert load_run(s, run_id) == result


def test_a_replay_rebuilds_the_run_without_a_model(env):
    run_id, result = record(env)
    replayed = replay_run(env[2], run_id)
    assert without_timing(replayed) == without_timing(result)


def test_an_e2e_run_replays_too(env):
    run_id, result = record(env, strategy="e2e")
    assert without_timing(replay_run(env[2], run_id)) == without_timing(result)


def test_a_run_that_breaks_is_kept_as_failed(env):
    settings, _, sessions = env
    loaded = load_claim_dir(ROOT / "10-voluntary-change")

    def boom():
        raise RuntimeError("the pipeline broke")

    with pytest.raises(RuntimeError):
        record_run(sessions, loaded.input, get_policy("cfsr"), settings, strategy="decomposed", backend="openai",
                   process=boom)
    with sessions() as s:
        (run,) = s.query(m.Run).all()
        assert run.status == "failed" and "the pipeline broke" in run.error and run.finished_at


def test_the_config_id_changes_with_any_prompt_or_setting(env):
    settings, _, sessions = env
    base = dict(strategy="decomposed", backend="openai",
                prompt_ids={n: p.id for n, p in prompts.all_prompts().items()})
    with sessions() as s:
        same = save_config(s, build_config(settings, **base))
        assert save_config(s, build_config(settings, **base)) == same
        edited = {**base, "prompt_ids": {**base["prompt_ids"], "judge": "0" * 64}}
        assert save_config(s, build_config(settings, **edited)) != same
        hotter = settings.model_copy(update={"openai_thinking_temperature": 0.7})
        assert save_config(s, build_config(hotter, **base)) != same


def test_a_benchmark_run_is_a_batch_of_recorded_runs(tmp_path, monkeypatch):
    root = tmp_path / "mini"
    for name in ("01-clean-medical-cancellation", "08-altered-booking-image"):
        shutil.copytree(ROOT / name, root / name)
    url = f"sqlite:///{tmp_path / 'bench.db'}"
    monkeypatch.setenv("CLAIMS_DATABASE_URL", url)
    bench.run(root, tmp_path / "out", None, "scripted", True, "t")
    bench.run(root, tmp_path / "out", None, "scripted", True, "t")              # the same benchmark again
    with open_sessions(url)() as s:
        (benchmark,) = s.query(m.Benchmark).all()
        assert benchmark.name == "mini" and [c.name for c in benchmark.cases] == sorted(p.name for p in root.iterdir())
        assert benchmark.cases[0].expected_outcome == "APPROVE" and benchmark.cases[0].expected_payout == 1240.0
        batches = s.query(m.Batch).all()
        assert len(batches) == 2 and batches[0].config_id == batches[1].config_id     # same code, prompts, settings
        assert all(len(b.runs) == 2 and all(r.status == "done" for r in b.runs) for b in batches)
        assert s.query(m.Claim).count() == 2                                           # claims stored once


def test_old_result_folders_import_as_batches(tmp_path, monkeypatch):
    from claims_pipeline.benchmark.importer import import_folder
    from claims_pipeline.benchmark.views import batch_results

    root = tmp_path / "mini"
    for name in ("01-clean-medical-cancellation", "05-theft-without-police-report"):
        shutil.copytree(ROOT / name, root / name)
    files = bench.run(root, tmp_path / "old-run", None, "scripted", True, "Old run", store=False)
    sessions = open_sessions(f"sqlite:///{tmp_path / 'import.db'}")
    assert "2 runs imported" in import_folder(sessions, tmp_path / "old-run")
    assert "already imported" in import_folder(sessions, tmp_path / "old-run")
    with sessions() as s:
        (batch,) = s.query(m.Batch).all()
        data = batch_results(s, batch.id)
        assert data["title"] == "Old run" and data["metrics"]["correct"] == files["correct"] == 2
        assert s.get(m.RunConfig, batch.config_id).config["imported"]["folder"] == "old-run"
        for run in batch.runs:
            stored = json.loads((tmp_path / "old-run" / f"{run.claim.name}.json").read_text())
            assert load_run(s, run.id).model_dump(mode="json") == stored


def test_one_place_decides_what_a_strategy_runs_on():
    from claims_pipeline.strategies import strategy

    loaded = load_claim_dir(ROOT / "01-clean-medical-cancellation")
    oracle = json.loads(loaded.oracle_path.read_text())
    result = strategy(Settings(backend="scripted"), oracle=oracle)(loaded.input, get_policy("cfsr"))
    assert result.decision.outcome.value == "APPROVE"
    with pytest.raises(Exception):
        strategy(Settings(backend="scripted"), "e2e")                  # the single-call strategy needs a real model
    with pytest.raises(ValueError):
        strategy(Settings(), "guess")


def test_a_benchmark_can_be_pruned_and_nothing_else_goes(tmp_path, monkeypatch):
    from sqlalchemy import func, select

    from claims_pipeline.store.prune import prune

    root = tmp_path / "mini"
    shutil.copytree(ROOT / "01-clean-medical-cancellation", root / "01-clean-medical-cancellation")
    url = f"sqlite:///{tmp_path / 'prune.db'}"
    monkeypatch.setenv("CLAIMS_DATABASE_URL", url)
    bench.run(root, tmp_path / "out", None, "scripted", True, "t")
    sessions = open_sessions(url)
    loaded = load_claim_dir(ROOT / "10-voluntary-change")                  # a submitted claim, kept
    record_run(sessions, loaded.input, get_policy("cfsr"), Settings(backend="scripted"), strategy="decomposed",
               backend="scripted", process=lambda: process_claim(
                   loaded.input, get_policy("cfsr"), ScriptedBackend.from_file(loaded.oracle_path)))
    with sessions() as s, s.begin():
        counts = prune(s, "mini")
    assert counts["run"] == 1 and counts["claim"] == 1 and counts["benchmark"] == 1
    with sessions() as s:
        assert [c.name for c in s.scalars(select(m.Claim))] == ["10-voluntary-change"]
        assert s.scalar(select(func.count()).select_from(m.Reading)) > 0       # the kept run is whole
        assert s.query(m.Benchmark).count() == 0 and s.query(m.Batch).count() == 0
