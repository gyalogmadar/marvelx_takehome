# Data layer migration plan

From "one JSON string per claim, files on disk, calls in a JSONL file" to a relational model of
the proof chain that a person who has read `policies/cfsr.py` can follow row by row.

## Status (2026-10-30)

Steps 0–5 are built; 6 (exports beyond today's files) and 7 (new UI views) are open. Where
the build differs from the plan below:

- **Claims have a name and a content id.** `claim.name` is how people and results refer to a
  claim (the API's id, a benchmark folder); `claim.id` is the sha256 of the name and the whole
  input. A regenerated mock with changed content is a new claim; old runs keep the old one.
  `claim.origin` is `submitted` or `benchmark`.
- **The description and the metadata are readings** with no document behind them (as in the
  result tree), so every observation points at a `reading` row.
- **The policy version is on the run** (`run.policy_id`), not in `run_config`, so one config
  covers a batch over claims under different policies.
- **`reading.transcript` is a JSON list of lines**, set only when a model transcribed them.
- **Model calls are written as they happen** (`run_scope` in `llm/calllog.py`) and list the
  prompt versions they were built from (`model_call.prompts`); `calls.jsonl` is still written
  next to the benchmark output.
- **Clef's questions and options are one prompt file**, `prompts/clef.json`.
- **Imported runs** (`store/importer.py`) have a config that records only what results.json
  said; their code and prompt versions are unknown.

## Why

Today:

| what | where |
|---|---|
| claim input | one `claims` row in `data/claims.db`; metadata and file list as JSON strings |
| the whole result tree (readings, facts, observations, checks, coverages, decision) | one JSON string in `claims.result` |
| uploaded files | `data/claims/<id>/`, found by filename |
| model calls | `<trace_dir>/calls.jsonl` + `blobs/`, joined by scanning for `call_id` |
| benchmark runs | `results/<run>/*.json`, read off disk by the API; their files found through `claims_root` |
| prompts | Python string constants; rendered prompts only inside `calls.jsonl` |

Consequences: the links in the proof chain (`facts_used`, `document_ids`, `determining`, quotes)
are unchecked strings; nothing is queryable ("every claim where `prior_knowledge` came back
unknown", "calibration of the judge over 200 runs"); reprocessing overwrites a result, so there
is no history; nothing records which prompt or setting produced a result; submitted claims and
benchmark claims live in two unrelated stores.

## Principles

1. **One vocabulary.** Tables are named after what a reader already met in `cfsr.py`
   (policy, facts, doc types, coverages, matches, proofs, checks, exclusions, red flags, payout)
   and in `domain/types.py` (reading, fact, observation, check result, coverage result,
   decision, model call). The policy declaration itself is stored, so every result row points
   at the line of policy that produced it.
2. **Input is immutable, runs are append-only.** A claim and its documents are written once.
   Every processing is a new run; a finished run is never edited.
3. **Reproducible.** A run records the exact code, policy, prompts and settings it used
   (`run_config`), and every model output, so it can be re-run under the same config or
   replayed with no model at all.
4. **Middle ground.** A list becomes rows when something joins on it or a person traces
   through it; display-only lists (alteration signs, unknowns) stay JSON columns.
5. **The pipeline stays storage-free.** It still builds the tree in memory; a repository saves
   and loads it. Tests keep running without a database. The one exception is the call log,
   written as calls happen, so a crash mid-claim keeps the calls already made.
6. **The API contract does not change** until a step says so: the UI keeps working throughout.

## The model

```
POLICY (as declared in cfsr.py; generated, read-only)
policy            id (sha256 of the policy module + wording), name, title, currency, wording, created_at
policy_fact       policy_id, name, type, hint                      -- Date("incident_date", "...")
policy_doc_type   policy_id, name, description                     -- doc_types={...}
policy_coverage   policy_id, name, title, proofs, payout           -- Coverage(...): proofs and payout as written
policy_check      policy_id, coverage NULL, section, check_id, kind, question, expect
                  -- section: matches | checks | exclusion | red_flag ; kind: judged | mechanical

PROMPTS
prompt            id (sha256 of text), name (role | read_document | extract_facts | judge | explain |
                  e2e | submission_stamp | ...), text, created_at

INPUT (immutable)
claim             id, policy (name), description, submitted_at, metadata (json), created_at
document          id, claim_id, filename, media_type, sha256, size, content (blob)

CONFIGURATION
run_config        id (sha256 of canonical json), config (json), created_at
                  -- code version (git commit + sha256 of the claims_pipeline sources), policy_id,
                  -- strategy, per component: provider, endpoint, model, effort, thinking, sampling,
                  -- max_tokens, prompt ids; judge, scorer, Clef model and floor

RUN (append-only)
run               id, claim_id, config_id, batch_id NULL, status, started_at, finished_at, error
reading           id, run_id, document_id, doc_type, language, issuer, transcript NULL,
                  alteration_signs (json), trust, trust_reasons (json), summary, scores (json),
                  model_call_id NULL
fact              id, run_id, name, type, value (json), confidence, note, scores (json)
observation       id, fact_id, source (reading | description | metadata), reading_id NULL, line,
                  quote, value (json), confidence, model_call_id NULL
coverage_result   id, run_id, coverage (name), status, payout, payout_note
check_result      id, run_id, coverage_result_id NULL, section, check_id, kind, verdict, rationale,
                  confidence, determining, quote, quote_reading_id NULL, quote_line NULL,
                  model_call_id NULL
check_evidence    check_result_id, fact_id NULL, reading_id NULL
decision          run_id (PK), outcome, confidence, summary, payout, currency, explanation,
                  unknowns (json)
model_call        id, run_id, component, provider, endpoint, model (as the server reported it),
                  system (json: prompt ids + policy_id), system_sha256, content (json: question ref,
                  rendered facts/readings, images as document refs), response (json), output_text,
                  reasoning, parsed (json), input/output/cache/reasoning tokens, latency_ms,
                  cost_usd, ok, error, created_at

BENCHMARKS
benchmark         id, name, title
benchmark_case    benchmark_id, claim_id, expected_outcome, expected_payout, note
batch             id, benchmark_id NULL, config_id, title, started_at
```

Notes on the choices:

- **`document` is the file; `reading` is the file as the reader understood it in one run.**
  Two runs may read the same image differently; that difference is data. A text file has no
  `transcript`: its lines come from `document.content`. The claim description and metadata are
  not files; an observation that quotes them records `source` instead.
- **Line references are `(reading_id, line)`**, checked on save (1 ≤ line ≤ line count, else
  stored NULL with the quote kept). The quote itself is kept: it is what the model claimed,
  and models misquote.
- **`check_result.section` and `check_id` are the policy's words** (`exclusion`,
  `prior_knowledge`; `checks`, `cancellation_proof_fits`). `check_result` joins to
  `policy_check` on `(run.config.policy_id, check_id)`; generated ids
  (`proofs:cancellation`, `within_period:incident_date`) are stored in `policy_check` too.
- **`check_evidence`** replaces the `facts_used` and `document_ids` string lists.
- **`fact.value` is JSON** (dates, periods, money, names all fit); SQLite generated columns
  can expose `value_date`/`value_number` later if SQL over fact values is needed.
- **Metrics are views**, not stored numbers: accuracy and confusion per batch, the per-claim
  comparison across batches, calibration (confidence bucket vs right/wrong) per component.

### Tracing a decision

```
decision                 DENY, determining: prior_knowledge
└─ check_result          exclusion / prior_knowledge: verdict fail, quote "Surgery scheduled 20 January" (reading d2, line 4)
   ├─ policy_check       "Did the claimant know about the incident, or the reason for it, before purchasing the policy?" expect no
   ├─ check_evidence  →  fact incident_date, fact purchase_date
   │                     └─ observation  booking-confirmation.md line 8 "Booked and paid on 2025-02-01"
   ├─ reading d2      →  document hospital-letter.md (sha256 …)
   └─ model_call         judge: request, reply, prompt ids → prompt "judge" (sha256 …)
```

## Prompts

Prompts move out of Python constants into files, one per component:

```
claims_pipeline/prompts/
  role.md               the analyst role (today: ROLE)
  submission_stamp.md   the "Current date is" rule (today: SUBMISSION_STAMP)
  read_document.md      today: the read_document task in llm/anthropic_backend.py
  extract_facts.md
  judge.md
  explain.md
  e2e.md                today: TASK in e2e.py
```

- A template may hold named slots filled at call time (`{doc_types}`, `{facts}`); the slot
  values come from the policy, never from the prompt file.
- On first use each prompt text is stored in `prompt` by hash. `run_config` lists the prompt
  ids per component, so a run's prompts are fixed even after the files change.
- `model_call.system` stores how the system prompt was assembled (role + submission stamp +
  `policy.id` for the wording + task prompt + slot values) and `system_sha256` of the rendered
  text. Replay and audit rebuild the exact text from the parts and check the hash.
- Clef's options (`JUDGE_OPTIONS`, `FACT_OPTIONS`, `ALTERED_OPTIONS`, the e2e outcomes) are
  prompts too and move the same way.

## Reproducibility

- **Re-run:** same claim + same `config_id` = same code, policy, prompts, models and settings.
  Model output can still vary; two batches under one config measure exactly that.
- **Replay:** a replay backend serves each component its stored `parsed` output from
  `model_call`, in order, with no model. Replaying a run under the current code rebuilds the
  tree; under its own code version it must rebuild it identically. Changing a rule in
  `decide()` and replaying last week's real runs shows which decisions the change moves.
  (`llm/scripted.py` is already this shape; replay is a second constructor for it.)
- The code version hashes the `claims_pipeline` sources as well as the git commit, so
  uncommitted edits produce a different config.

## Code layout

```
claims_pipeline/store/
  models.py       SQLAlchemy 2 typed mappings, one class per table above
  repository.py   create_claim, start_run, save_run(result) -> run_id, load_run(run_id) -> ClaimResult,
                  list_claims, runs_of(claim_id); policy and prompt snapshots
  calls.py        the call log's database sink (replaces the JSONL writer as the default)
  views.py        metric queries: batch accuracy, confusion, comparison, calibration
  replay.py       the replay backend over stored model calls
  importer.py     one-off import of data/claims.db and results/*/ (with their calls.jsonl)
  export.py       results/<batch>/ markdown + JSON, calls.jsonl, for git and fine-tuning data
migrations/       Alembic
```

SQLAlchemy 2 (typed mappings, kept separate from the Pydantic domain types) with Alembic for
migrations: every schema change is a reviewed migration, applied with `alembic upgrade head`.
SQLite in WAL mode; nothing SQLite-only, so Postgres stays a configuration change. New
dependencies: `sqlalchemy (>=2.0)`, `alembic (>=1.13)`.

## Steps

Each step ends green: all tests pass, the scripted mock benchmark is 35/35, the UI works.

**0. Groundwork in the domain (no database).**
- Rename `domain.Document` to `Reading` (about 13 Python files and 4 UI files; JSON field names
  `documents` → `readings` with the API keeping `documents` as an alias until step 7).
- Add `call_id` to `Reading`, `Observation` and `CheckResult` (the tracer already knows it).
- Done when: tests pass, results unchanged apart from the new ids.

**1. Prompts as files.**
- Golden test first: render every prompt for mock 01 (and an image mock, and e2e, and the
  Clef requests) through a fake client and store the exact text.
- Move the constants into `claims_pipeline/prompts/`; load them in the backends.
- Done when: the golden renders are byte-identical.

**2. Schema and repository, with the round trip as the safety net.**
- `models.py`, `repository.py`, the first Alembic migration; policy and prompt snapshots.
- Round-trip test: for each of the 35 mock claims (scripted) and each JSON result under
  `results/` from the Qwen runs, `load_run(save_run(result)) == result`.
- Done when: every round trip is exact.

**3. Run config and replay.**
- Build `run_config` from `Settings` + code hash + policy id + prompt ids; content-addressed.
- `model_call` rows with `system` parts and `system_sha256`; the replay backend.
- Done when: a recorded run replays to an identical tree, and a rebuilt system prompt matches
  its stored hash for every call in the round-trip set.

**4. API on the repository.**
- `POST /claims` creates claim + documents + run; `POST /claims/{id}/runs` reprocesses;
  `GET /claims/{id}` returns the latest run in today's shape; `GET /claims/{id}/runs` lists them.
- Documents served from `document.content`; calls from `model_call`.
- `api/store.py` and the files under `data/claims/` are no longer used.
- Done when: the UI end-to-end flow works unchanged (submit, open, evidence links, calls).

**5. Benchmarks in the database.**
- The runner loads claim folders into `claim`/`document` + `benchmark_case`, writes a `batch`
  and its runs; `results.md` and `compare` are generated from `views.py`.
- `/benchmarks` endpoints read batches; no API read touches `results/` or `claims_root`.
- Importer: `data/claims.db` and every `results/*/` (results.json, per-claim JSON,
  calls.jsonl, blobs) become claims, batches, runs and calls.
- Done when: every existing run shows in the UI from the database with the same numbers as
  its `results.md`.

**6. Exports.**
- `export.py` writes `results/<batch>/` (markdown and JSON) and `calls.jsonl` on demand, for
  git and for per-component fine-tuning data.

**7. What the data now allows (UI).**
- Run history per claim; the same claim across batches side by side.
- Calibration per component (the confidence-score evidence).
- "Every claim where check X was unknown", from the policy page.
- Drop the `documents` alias.

## Risks

- **Rename churn (step 0)** touches many files at once; it goes first, alone, while nothing
  else moves.
- **Prompt rebuild drift:** an assembly change silently changes prompts. The golden test
  (step 1) and `system_sha256` (step 3) catch it.
- **Import of older runs:** early runs predate fields (`red_flags`, `scores`, `call_id`); the
  importer fills defaults and records the gap rather than failing.
- **Concurrency:** SQLite WAL with one writer per run is enough for the API's background
  tasks; a long benchmark and API submissions write concurrently, so writes stay short
  transactions.

## Out of scope

Authentication, multi-tenant separation, object storage for documents, Postgres deployment.
