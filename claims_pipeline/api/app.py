"""FastAPI surface.

    POST /claims                       submit (multipart); 202 + claim_id, processed in the background
    GET  /claims                       summaries, newest first
    GET  /claims/{id}                  status + the full result tree
    GET  /claims/{id}/documents/{doc}  the uploaded file behind a document id
    GET  /policies, /policies/{name}   the composition as data, plus the policy text
    GET  /benchmarks, /benchmarks/{run} benchmark runs found under results/ (+ their claims and documents)
    GET  /health                       backend + component configuration

The React UI (ui/dist) is served under /ui/ when it has been built; / redirects there.
The assignment's endpoints keep their exact paths, so the UI lives under its own prefix.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from claims_pipeline.llm import Settings
from claims_pipeline.llm.base import ModelBackend
from claims_pipeline.llm.scripted import ScriptedBackend
from claims_pipeline.pipeline import ClaimFile, ClaimInput, process_claim
from claims_pipeline.policies import POLICIES, get_policy

from .store import ClaimStore

ORACLE_FILENAME = "oracle.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
UI_DIST = PROJECT_ROOT / "ui" / "dist"
RESULTS_DIR = PROJECT_ROOT / "results"


def make_backend(settings: Settings, files: list[ClaimFile]) -> tuple[ModelBackend, list[ClaimFile]]:
    """Choose the backend; with the scripted backend an uploaded oracle.json is consumed, never treated as evidence."""
    oracle = next((f for f in files if f.filename == ORACLE_FILENAME), None)
    evidence = [f for f in files if f is not oracle]
    if settings.backend in ("anthropic", "openai"):
        from claims_pipeline.llm.clients import make_decomposed_backend

        return make_decomposed_backend(settings), evidence
    oracle_data = json.loads(oracle.content) if oracle else {}
    return ScriptedBackend(oracle_data, strict=False), evidence


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.store = ClaimStore(Path(settings.data_dir))
        yield

    app = FastAPI(title="Claims pipeline", version="0.1.0", lifespan=lifespan,
                  description="Policy-as-code insurance claim processing with an auditable evidence tree.")

    def store(request: Request) -> ClaimStore:
        return request.app.state.store

    def run_claim(claim: ClaimInput, policy_name: str, backend: ModelBackend | None, st: ClaimStore, strategy: str) -> None:
        try:
            if strategy == "e2e":
                from claims_pipeline.e2e import process_claim_e2e
                from claims_pipeline.llm.clients import make_client

                result = process_claim_e2e(claim, get_policy(policy_name), make_client(settings))
            else:
                result = process_claim(claim, get_policy(policy_name), backend)
            st.set_result(claim.claim_id, result)
        except Exception as exc:  # the record carries the failure; the API keeps serving
            st.set_error(claim.claim_id, f"{type(exc).__name__}: {exc}")

    @app.get("/health")
    def health(request: Request):
        return {"status": "ok", "backend": settings.backend, "strategies": ["decomposed", "e2e"],
                "components": {k: v.model_dump() for k, v in settings.components().items()},
                "policies": sorted(POLICIES)}

    @app.get("/policies")
    def list_policies():
        return [p.describe() for p in POLICIES.values()]

    @app.get("/policies/{name}")
    def get_policy_route(name: str):
        if name not in POLICIES:
            raise HTTPException(404, f"unknown policy {name!r}")
        p = POLICIES[name]
        return {**p.describe(), "text": p.text}

    @app.post("/claims", status_code=202)
    async def submit_claim(
        request: Request,
        background: BackgroundTasks,
        description: str = Form(...),
        policy: str = Form(default=None),
        metadata: str = Form(default="{}"),
        submitted_at: str = Form(default=None),
        strategy: str = Form(default="decomposed"),
        wait: bool = Form(default=False),
        files: list[UploadFile] = File(default=[]),
    ):
        if strategy not in ("decomposed", "e2e"):
            raise HTTPException(400, "strategy must be 'decomposed' or 'e2e'")
        if strategy == "e2e" and settings.backend == "scripted":
            raise HTTPException(400, "the e2e strategy needs a real model: CLAIMS_BACKEND=anthropic or openai")
        policy_name = policy or settings.default_policy
        if policy_name not in POLICIES:
            raise HTTPException(400, f"unknown policy {policy_name!r}; known: {sorted(POLICIES)}")
        try:
            meta = {str(k): str(v) for k, v in json.loads(metadata or "{}").items()}
        except (json.JSONDecodeError, AttributeError):
            raise HTTPException(400, "metadata must be a JSON object")
        when = datetime.fromisoformat(submitted_at).replace(tzinfo=timezone.utc) if submitted_at else datetime.now(timezone.utc)

        claim_id = uuid.uuid4().hex[:12]
        uploaded = [ClaimFile(filename=f.filename or f"file{i}", content=await f.read()) for i, f in enumerate(files)]
        backend, evidence = make_backend(settings, uploaded)
        st = store(request)
        folder = st.files_dir(claim_id)
        for f in evidence:
            (folder / Path(f.filename).name).write_bytes(f.content)
        st.create(claim_id, policy_name, description, meta, when, [f.filename for f in evidence])
        claim = ClaimInput(claim_id=claim_id, description=description, files=evidence, metadata=meta, submitted_at=when)

        if wait:
            run_claim(claim, policy_name, backend, st, strategy)
            return JSONResponse(status_code=200, content=st.get(claim_id))
        background.add_task(run_claim, claim, policy_name, backend, st, strategy)
        return {"claim_id": claim_id, "status": "processing", "policy": policy_name, "strategy": strategy,
                "links": {"self": f"/claims/{claim_id}"}}

    @app.get("/claims")
    def list_claims(request: Request):
        return store(request).list()

    @app.get("/claims/{claim_id}")
    def get_claim(claim_id: str, request: Request):
        rec = store(request).get(claim_id)
        if rec is None:
            raise HTTPException(404, "no such claim")
        return rec

    @app.get("/claims/{claim_id}/documents/{doc_id}")
    def get_document(claim_id: str, doc_id: str, request: Request):
        rec = store(request).get(claim_id)
        if rec is None:
            raise HTTPException(404, "no such claim")
        doc = next((d for d in (rec.get("result") or {}).get("documents", []) if d["id"] == doc_id), None)
        if doc is None or doc["kind"] in ("description", "metadata"):
            raise HTTPException(404, "no such document")
        path = store(request).files_dir(claim_id) / Path(doc["filename"]).name
        if not path.exists():
            raise HTTPException(404, "file missing on disk")
        return FileResponse(path, media_type=doc.get("media_type") or "text/plain; charset=utf-8")

    # -- benchmark runs: read-only views over results/<run>/ --------------------------------
    def benchmark_dir(run: str) -> Path:
        d = RESULTS_DIR / Path(run).name
        if not (d / "results.json").exists():
            raise HTTPException(404, f"no benchmark run {run!r}")
        return d

    @app.get("/benchmarks")
    def list_benchmarks():
        runs = []
        if RESULTS_DIR.exists():
            for d in sorted(RESULTS_DIR.iterdir()):
                if (d / "results.json").exists():
                    data = json.loads((d / "results.json").read_text())
                    m = data.get("metrics", {})
                    runs.append({"name": d.name, "title": data.get("title", d.name), "backend": data.get("backend"),
                                 "run_at": data.get("run_at"), "claims": m.get("claims"), "scored": m.get("scored"),
                                 "correct": m.get("correct"), "accuracy": m.get("accuracy"),
                                 "total_cost_usd": m.get("total_cost_usd")})
        return runs

    @app.get("/benchmarks/{run}")
    def get_benchmark(run: str):
        return json.loads((benchmark_dir(run) / "results.json").read_text())

    @app.get("/benchmarks/{run}/claims/{claim_id}")
    def get_benchmark_claim(run: str, claim_id: str):
        path = benchmark_dir(run) / f"{Path(claim_id).name}.json"
        if not path.exists():
            raise HTTPException(404, "no result for that claim in this run")
        data = json.loads((benchmark_dir(run) / "results.json").read_text())
        row = next((r for r in data.get("claims", []) if r["claim_id"] == claim_id), {})
        return {"claim_id": claim_id, "status": "done", "policy": row.get("policy"), "submitted_at": None,
                "result": json.loads(path.read_text()), "expected": row.get("expected"),
                "expected_payout": row.get("expected_payout"), "note": row.get("note"), "files": [], "error": None}

    @app.get("/benchmarks/{run}/claims/{claim_id}/documents/{doc_id}")
    def get_benchmark_document(run: str, claim_id: str, doc_id: str):
        d = benchmark_dir(run)
        data = json.loads((d / "results.json").read_text())
        result_path = d / f"{Path(claim_id).name}.json"
        if not result_path.exists():
            raise HTTPException(404, "no such claim")
        result = json.loads(result_path.read_text())
        doc = next((x for x in result.get("documents", []) if x["id"] == doc_id), None)
        if doc is None or doc["kind"] in ("description", "metadata"):
            raise HTTPException(404, "no such document")
        path = Path(data["claims_root"]) / Path(claim_id).name / Path(doc["filename"]).name
        if not path.exists():
            raise HTTPException(404, "file missing on disk")
        return FileResponse(path, media_type=doc.get("media_type") or "text/plain; charset=utf-8")

    if UI_DIST.exists():
        @app.get("/", include_in_schema=False)
        def root():
            return RedirectResponse("/ui/")

        @app.get("/ui/{path:path}", include_in_schema=False)
        def ui(path: str):
            """Static assets by path; anything else falls back to index.html so client-side routes deep-link."""
            target = (UI_DIST / path).resolve()
            if path and target.is_file() and UI_DIST.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(UI_DIST / "index.html")

    return app


app = create_app()
