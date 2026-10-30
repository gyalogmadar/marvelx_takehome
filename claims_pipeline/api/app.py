"""FastAPI surface.

    POST /claims                       submit (multipart); 202 + claim_id, processed in the background
    GET  /claims                       submitted claims, newest first
    GET  /claims/{id}                  status + the full result tree of its latest run (?run=<run_id> for another)
    POST /claims/{id}/runs             process the claim again (current code, prompts and settings): a new run
    GET  /claims/{id}/runs             every run of the claim: when, under which config, what it decided
    GET  /claims/{id}/documents/{doc}  the uploaded file behind a reading
    GET  /claims/{id}/calls            every model call of the run, in full
    GET  /policies, /policies/{name}   the composition as data, plus the policy text
    GET  /benchmarks, /benchmarks/{id} recorded benchmark executions (+ their claims, documents and calls)
    GET  /health                       backend + component configuration

Everything is read from the store (claims_pipeline/store, CLAIMS_DATABASE_URL); nothing is
read back from files.  The React UI (ui/dist) is served under /ui/ when it has been built;
/ redirects there.  The assignment's endpoints keep their exact paths.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from claims_pipeline.llm import Settings
from claims_pipeline.llm.base import ModelBackend
from claims_pipeline.llm.scripted import ScriptedBackend
from claims_pipeline.pipeline import ClaimFile, ClaimInput, process_claim
from claims_pipeline.policies import POLICIES, get_policy
from claims_pipeline.store import models as m
from claims_pipeline.store import queries
from claims_pipeline.store.db import sessions as open_sessions
from claims_pipeline.store.replay import claim_input
from claims_pipeline.store.runs import finish_run, prepare_run

ORACLE_FILENAME = "oracle.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
UI_DIST = PROJECT_ROOT / "ui" / "dist"


def make_backend(settings: Settings, oracle: dict | None) -> ModelBackend:
    """The decomposed backend: the configured model, or (scripted) the oracle uploaded with the claim."""
    if settings.backend in ("anthropic", "openai"):
        from claims_pipeline.llm.clients import make_decomposed_backend

        return make_decomposed_backend(settings)
    from claims_pipeline.llm.clients import with_routes

    return with_routes(ScriptedBackend(oracle or {}, strict=False), settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.sessions = open_sessions(settings.database_url)
        yield

    app = FastAPI(title="Claims pipeline", version="0.2.0", lifespan=lifespan,
                  description="Policy-as-code insurance claim processing with an auditable evidence tree.")

    def sessions(request: Request):
        return request.app.state.sessions

    def process(claim: ClaimInput, policy_name: str, strategy: str, oracle: dict | None):
        def run():
            policy = get_policy(policy_name)
            if strategy == "e2e":
                from claims_pipeline.e2e import process_claim_e2e
                from claims_pipeline.llm.clients import make_client

                return process_claim_e2e(claim, policy, make_client(settings))
            if strategy == "clef":
                from claims_pipeline.clef_e2e import process_claim_clef
                from claims_pipeline.llm.clef_backend import ClefClient

                return process_claim_clef(claim, policy, ClefClient(settings))
            return process_claim(claim, policy, make_backend(settings, oracle))
        return run

    def start(request: Request, claim: ClaimInput, policy_name: str, strategy: str, oracle: dict | None):
        """Store the claim and open a run; return (run_id, the work that completes it)."""
        backend = "clef" if strategy == "clef" else settings.backend
        run_id = prepare_run(sessions(request), claim, get_policy(policy_name), settings, strategy=strategy,
                             backend=backend)

        def work():
            try:
                finish_run(sessions(request), run_id, process(claim, policy_name, strategy, oracle))
            except Exception:  # the run is stored as failed, with the error; the API keeps serving
                pass
        return run_id, work

    def check_strategy(strategy: str) -> None:
        if strategy not in ("decomposed", "e2e", "clef"):
            raise HTTPException(400, "strategy must be 'decomposed', 'e2e' or 'clef'")
        if strategy == "clef" and not settings.clef_base_url:
            raise HTTPException(400, "the clef strategy needs CLAIMS_CLEF_BASE_URL")
        if strategy == "e2e" and settings.backend == "scripted":
            raise HTTPException(400, "the e2e strategy needs a real model: CLAIMS_BACKEND=anthropic or openai")

    @app.get("/health")
    def health(request: Request):
        return {"status": "ok", "backend": settings.backend, "strategies": ["decomposed", "e2e", "clef"],
                "judge_backend": settings.judge_backend or None,
                "scorer": settings.scorer or None,
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
        check_strategy(strategy)
        policy_name = policy or settings.default_policy
        if policy_name not in POLICIES:
            raise HTTPException(400, f"unknown policy {policy_name!r}; known: {sorted(POLICIES)}")
        try:
            meta = {str(k): str(v) for k, v in json.loads(metadata or "{}").items()}
        except (json.JSONDecodeError, AttributeError):
            raise HTTPException(400, "metadata must be a JSON object")
        when = datetime.fromisoformat(submitted_at).replace(tzinfo=timezone.utc) if submitted_at else datetime.now(timezone.utc)

        uploaded = [ClaimFile(filename=f.filename or f"file{i}", content=await f.read()) for i, f in enumerate(files)]
        # with the scripted backend an uploaded oracle.json is the model's answers, never evidence
        oracle_file = next((f for f in uploaded if f.filename == ORACLE_FILENAME), None)
        oracle = json.loads(oracle_file.content) if oracle_file else None
        evidence = [f for f in uploaded if f is not oracle_file]
        claim_id = uuid.uuid4().hex[:12]
        claim = ClaimInput(claim_id=claim_id, description=description, files=evidence, metadata=meta, submitted_at=when)
        run_id, work = start(request, claim, policy_name, strategy, oracle)
        if wait:
            work()
            with sessions(request)() as s:
                return JSONResponse(status_code=200, content=queries.claim_record(s, claim_id))
        background.add_task(work)
        return {"claim_id": claim_id, "run_id": run_id, "status": "processing", "policy": policy_name,
                "strategy": strategy, "links": {"self": f"/claims/{claim_id}"}}

    @app.get("/claims")
    def list_claims(request: Request):
        with sessions(request)() as s:
            return queries.claim_summaries(s)

    @app.get("/claims/{claim_id}")
    def get_claim(claim_id: str, request: Request, run: str | None = None):
        with sessions(request)() as s:
            rec = queries.claim_record(s, claim_id, run)
        if rec is None:
            raise HTTPException(404, "no such claim" if run is None else "no such run of this claim")
        return rec

    @app.post("/claims/{claim_id}/runs", status_code=202)
    def rerun_claim(claim_id: str, request: Request, background: BackgroundTasks, strategy: str = "decomposed",
                    wait: bool = False):
        """Process a stored claim again under today's code, prompts and settings.  Earlier runs stay."""
        check_strategy(strategy)
        with sessions(request)() as s:
            claim = queries.latest_claim(s, claim_id)
            if claim is None:
                raise HTTPException(404, "no such claim")
            inp, policy_name = claim_input(claim), claim.policy
        run_id, work = start(request, inp, policy_name, strategy, None)
        if wait:
            work()
            with sessions(request)() as s:
                return JSONResponse(status_code=200, content=queries.claim_record(s, claim_id, run_id))
        background.add_task(work)
        return {"claim_id": claim_id, "run_id": run_id, "status": "processing",
                "links": {"self": f"/claims/{claim_id}?run={run_id}"}}

    @app.get("/claims/{claim_id}/runs")
    def list_runs(claim_id: str, request: Request):
        with sessions(request)() as s:
            runs = queries.runs_of(s, claim_id)
        if runs is None:
            raise HTTPException(404, "no such claim")
        return runs

    def run_of(request: Request, claim_id: str, run: str | None) -> str:
        with sessions(request)() as s:
            rec = queries.claim_record(s, claim_id, run)
        if rec is None or rec["run_id"] is None:
            raise HTTPException(404, "no such claim or run")
        return rec["run_id"]

    @app.get("/claims/{claim_id}/calls")
    def get_claim_calls(claim_id: str, request: Request, run: str | None = None, component: str | None = None):
        run_id = run_of(request, claim_id, run)
        with sessions(request)() as s:
            return queries.call_records(s, run_id, component)

    def file_response(request: Request, run_id: str, doc_id: str) -> Response:
        with sessions(request)() as s:
            doc = queries.document(s, run_id, doc_id)
            if doc is None:
                raise HTTPException(404, "no such document")
            return Response(doc.content, media_type=doc.media_type or "text/plain; charset=utf-8")

    @app.get("/claims/{claim_id}/documents/{doc_id}")
    def get_document(claim_id: str, doc_id: str, request: Request, run: str | None = None):
        return file_response(request, run_of(request, claim_id, run), doc_id)

    # -- benchmarks: recorded executions (batches) ---------------------------------------------
    def batch_id(run: str) -> int:
        if not run.isdigit():
            raise HTTPException(404, f"no benchmark run {run!r}")
        return int(run)

    @app.get("/benchmarks")
    def list_benchmarks(request: Request):
        with sessions(request)() as s:
            return queries.batches(s)

    @app.get("/benchmarks/{run}")
    def get_benchmark(run: str, request: Request):
        with sessions(request)() as s:
            data = queries.batch_results(s, batch_id(run))
        if data is None:
            raise HTTPException(404, f"no benchmark run {run!r}")
        return data

    def batch_run_id(request: Request, run: str, claim_id: str) -> str:
        with sessions(request)() as s:
            rec = queries.batch_claim(s, batch_id(run), claim_id)
        if rec is None or rec["run_id"] is None:
            raise HTTPException(404, "no result for that claim in this run")
        return rec["run_id"]

    @app.get("/benchmarks/{run}/claims/{claim_id}")
    def get_benchmark_claim(run: str, claim_id: str, request: Request):
        with sessions(request)() as s:
            rec = queries.batch_claim(s, batch_id(run), claim_id)
        if rec is None:
            raise HTTPException(404, "no result for that claim in this run")
        return rec

    @app.get("/benchmarks/{run}/claims/{claim_id}/calls")
    def get_benchmark_claim_calls(run: str, claim_id: str, request: Request, component: str | None = None):
        run_id = batch_run_id(request, run, claim_id)
        with sessions(request)() as s:
            return queries.call_records(s, run_id, component)

    @app.get("/benchmarks/{run}/claims/{claim_id}/documents/{doc_id}")
    def get_benchmark_document(run: str, claim_id: str, doc_id: str, request: Request):
        return file_response(request, batch_run_id(request, run, claim_id), doc_id)

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
