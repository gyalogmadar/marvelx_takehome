"""What a run was made with, as one hashed record.

Two runs with the same ``run_config.id`` were made by the same code and prompt
versions, with the same strategy, models and settings.  Change any of those (an
edited prompt file, a sampling setting, an uncommitted code edit) and the id
changes with it.  The policy version is the run's own (``run.policy_id``): one
config covers a batch over claims under different policies.

    code       the git commit, and the sha256 of every .py file under claims_pipeline/
               (so an uncommitted edit is a different config too)
    prompts    {name: prompt.id} for every file in claims_pipeline/prompts/
    run        strategy, backend, judge, scorer, explain
    settings   every setting, minus secrets and local paths
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from claims_pipeline.llm.config import Settings

from . import models as m
from .repository import now

PACKAGE = Path(__file__).resolve().parents[1]
NOT_RECORDED = {"trace_dir", "trace", "database_url"}  # where things are written, not how


@cache
def code_version() -> dict[str, Any]:
    digest = hashlib.sha256()
    for path in sorted(PACKAGE.rglob("*.py")):
        digest.update(str(path.relative_to(PACKAGE)).encode() + b"\0" + path.read_bytes() + b"\0")
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PACKAGE, capture_output=True, text=True,
                                timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"git_commit": commit, "source_sha256": digest.hexdigest()}


def build_config(settings: Settings, *, strategy: str, backend: str, prompt_ids: dict[str, str],
                 explain: bool = True) -> dict[str, Any]:
    recorded = {k: v for k, v in settings.model_dump().items()
                if k not in NOT_RECORDED and "api_key" not in k}
    return {
        "code": code_version(),
        "prompts": dict(sorted(prompt_ids.items())),
        "run": {"strategy": strategy, "backend": backend, "judge": settings.judge_backend or None,
                "scorer": settings.scorer or None, "explain": explain},
        "settings": recorded,
    }


def save_config(s: Session, config: dict[str, Any]) -> str:
    """Store a config once; return its id (the sha256 of its canonical JSON)."""
    cid = hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if s.get(m.RunConfig, cid) is None:
        s.add(m.RunConfig(id=cid, config=config, created_at=now()))
        s.flush()
    return cid
