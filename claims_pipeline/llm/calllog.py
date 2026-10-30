"""The call log: every model call, complete, one JSON line each.

A record holds what was sent and what came back, unabridged:

    call_id, ts, claim_id, strategy, component, provider, endpoint, model
    request      the exact request body (images replaced by a blob reference)
    response     the raw response document as the server returned it
    output_text  the reply text the structured output was parsed from
    reasoning    the model's reasoning text when it returned any
                 (OpenAI-compatible: ``reasoning_content`` or an inline <think> block;
                 Anthropic: the summarized thinking blocks)
    parsed       the validated structured output, or null
    prompts      the prompt versions the request was built from: [{name, id}], id = sha256
                 of the text in claims_pipeline/prompts/
    usage        input / output / cached / reasoning tokens
    latency_ms, error

Images are written once to ``blobs/<sha256>.<ext>`` next to ``calls.jsonl``; the
request keeps ``{"blob": "blobs/…", "sha256", "bytes", "media_type"}`` in their
place, so the request can be rebuilt byte for byte. Failed calls are logged too,
with whatever the server answered.

Which claim a call belongs to comes from ``claim_scope`` (a context variable set
by the pipeline), so the clients need no extra arguments.  Inside ``run_scope``
(set by whoever stores the run: the API, the benchmark runner) each call is also
written to the database as a ``model_call`` row of that run, as it happens.

    python -m claims_pipeline.llm.calllog results/decomposed/trace --claim "claim 7" --component judge
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import contextvars
import datetime as dt
import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator

from claims_pipeline import prompts

__all__ = ["CallLog", "claim_scope", "run_scope", "current_scope", "redact_images", "read", "main"]

_SCOPE: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("claims_call_scope", default={})
_STORE: contextvars.ContextVar[Any] = contextvars.ContextVar("claims_call_store", default=None)
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


@contextlib.contextmanager
def claim_scope(claim_id: str, strategy: str) -> Iterator[None]:
    token = _SCOPE.set({**_SCOPE.get(), "claim_id": claim_id, "strategy": strategy})
    try:
        with prompts.noting():
            yield
    finally:
        _SCOPE.reset(token)


@contextlib.contextmanager
def run_scope(run_id: str, sessions) -> Iterator[None]:
    """Calls made inside belong to this run and are written to the store ``sessions`` opens."""
    token, store = _SCOPE.set({**_SCOPE.get(), "run_id": run_id}), _STORE.set(sessions)
    try:
        yield
    finally:
        _SCOPE.reset(token)
        _STORE.reset(store)


def current_scope() -> dict[str, str]:
    return dict(_SCOPE.get())


class CallLog:
    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.path = self.dir / "calls.jsonl"
        self._lock = threading.Lock()

    def _blob(self, data_b64: str, media_type: str) -> dict[str, Any]:
        raw = base64.b64decode(data_b64)
        sha = hashlib.sha256(raw).hexdigest()
        rel = f"blobs/{sha}.{_EXT.get(media_type, 'bin')}"
        target = self.dir / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        return {"blob": rel, "sha256": sha, "bytes": len(raw), "media_type": media_type}

    def record(self, *, component: str, provider: str, endpoint: str, model: str,
               request: Any, response: Any = None, output_text: str | None = None,
               reasoning: str | None = None, parsed: Any = None, usage: dict[str, Any] | None = None,
               latency_ms: int = 0, error: str | None = None) -> str:
        call_id = uuid.uuid4().hex
        rec = {
            "call_id": call_id,
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
            **current_scope(),
            "component": component, "provider": provider, "endpoint": endpoint, "model": model,
            "request": redact_images(request, self._blob),
            "response": response, "output_text": output_text, "reasoning": reasoning,
            "parsed": parsed, "prompts": prompts.take_noted(), "usage": usage or {},
            "latency_ms": latency_ms, "error": error,
        }
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        sessions = _STORE.get()
        if sessions is not None and rec.get("run_id"):
            from claims_pipeline.store.calls import store_call

            store_call(sessions, {**rec, "request": redact_images(request, _image_ref)})
        return call_id


def _image_ref(data_b64: str, media_type: str) -> dict[str, Any]:
    """An image as the database keeps it in a request: what it was, not the bytes (the
    document holds those)."""
    raw = base64.b64decode(data_b64)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "media_type": media_type}


def redact_images(obj: Any, store) -> Any:
    """A deep copy of a request with every base64 image (Anthropic ``source`` blocks,
    OpenAI ``image_url`` data URLs, and bare data-URL strings as Clef takes them)
    replaced by the blob reference ``store`` returns."""
    if isinstance(obj, str) and obj.startswith("data:image/") and ";base64," in obj:
        head, data = obj.split(";base64,", 1)
        return store(data, head[5:])
    if isinstance(obj, dict):
        src = obj.get("source")
        if obj.get("type") == "image" and isinstance(src, dict) and src.get("type") == "base64":
            return {**obj, "source": {"type": "base64", **store(src["data"], src["media_type"])}}
        url = (obj.get("image_url") or {}).get("url") if obj.get("type") == "image_url" else None
        if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
            head, data = url.split(";base64,", 1)
            return {**obj, "image_url": store(data, head[5:])}
        return {k: redact_images(v, store) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_images(v, store) for v in obj]
    return obj


def read(directory: Path | str, claim_id: str | None = None,
         component: str | None = None) -> list[dict[str, Any]]:
    path = Path(directory) / "calls.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if claim_id is not None and rec.get("claim_id") != claim_id:
            continue
        if component is not None and rec.get("component") != component:
            continue
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print logged model calls readably.")
    ap.add_argument("trace_dir", type=Path)
    ap.add_argument("--claim", default=None)
    ap.add_argument("--component", default=None)
    ap.add_argument("--full", action="store_true", help="print the whole request and response JSON")
    args = ap.parse_args(argv)
    for rec in read(args.trace_dir, args.claim, args.component):
        u = rec.get("usage") or {}
        print(f"=== {rec['ts']} {rec.get('claim_id', '-')} {rec['component']} {rec['model']} "
              f"{rec['latency_ms']} ms in={u.get('input_tokens', 0)} out={u.get('output_tokens', 0)} "
              f"reasoning={u.get('reasoning_tokens', 0)} {'ERROR ' + rec['error'] if rec.get('error') else ''}")
        if args.full:
            print(json.dumps({"request": rec["request"], "response": rec["response"]}, indent=2, ensure_ascii=False))
        else:
            req = rec["request"]
            msgs = req.get("messages", [])
            system = req.get("system")
            if isinstance(system, list):
                system = "\n".join(b.get("text", "") for b in system)
            if system is None and msgs and msgs[0].get("role") == "system":
                system = msgs[0]["content"]
            print("--- system\n" + str(system or "")[-1500:])
            for m in msgs:
                if m.get("role") == "system":
                    continue
                parts = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
                for p in parts:
                    print(f"--- {m['role']}: " + (p.get("text") or json.dumps(p, ensure_ascii=False))[:4000])
        if rec.get("reasoning"):
            print("--- reasoning\n" + rec["reasoning"])
        print("--- output\n" + (rec.get("output_text") or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
