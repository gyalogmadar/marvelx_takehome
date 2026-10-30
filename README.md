# Claims pipeline

An insurance claim processing pipeline where **the policy is code, the model reads, and code decides**.

A claim comes in as a description plus supporting documents (text, markdown, images in any language).
The pipeline reads every document, extracts the facts the policy cares about, runs the policy's checks,
and returns **APPROVE**, **DENY** or **UNCERTAIN** with a payout, a confidence, and an evidence tree that
links the decision to the exact document lines it rests on.

```
read documents  ->  extract facts  ->  assess trust  ->  run checks  ->  decide  ->  narrate
   (model)           (model)            (code)        (code + model)    (code)     (model)
```

![Evidence view: a referred claim, hovering a fact lights the source line](docs/screenshots/evidence-view.png)

## Why it looks like this

* **Policy as code.** A policy is one short Python file composed from about ten components:
  typed facts, proof rules, mechanical checks, one `judged` check for questions that need reading, a
  coverage, a payout formula. `claims_pipeline/policies/cfsr.py` reads line by line against
  `policy.md`; `gadget.py` is a second, deliberately different policy that shows a new policy is a new
  file of the same size and nothing else. See [docs/AUTHORING.md](docs/AUTHORING.md).
* **Code decides.** Every check answers *pass*, *fail* or *unknown*. Any failed exclusion denies. A
  matched coverage with its proofs present and every check passed approves. Anything still open is
  referred to a human with a list of what would resolve it. The model never chooses the outcome; it
  transcribes, extracts, answers narrow yes/no questions with a quote, and writes the explanation.
* **One data model.** The result tree (`claims_pipeline/domain/types.py`) is what the API returns,
  what the UI renders, what the benchmark scores and what the audit trail stores. Every fact carries
  its observations (document, line, quote, confidence); every check names the facts it consumed.
* **Swappable model components.** The four model-backed steps sit behind one interface
  (`claims_pipeline/llm/base.py`). Each has its own model and effort setting, every call is traced
  with tokens, cache hits, latency and cost, and a scripted backend replays recorded answers so the
  whole system runs offline and deterministically. That is also the seam for prompt tuning or
  fine-tuning a single component later.

## Two strategies, one comparison

The repository runs the same claims two ways and compares them:

| | **decomposed** (default) | **e2e** |
|---|---|---|
| what the model does | reads one document at a time, extracts facts with quotes, answers narrow yes/no questions | receives the policy, the task, the description and every document in one prompt and returns the decision |
| what code does | trust assessment, every mechanical check, the decision rule, the payout | nothing: the model's answer is the result |
| audit trail | decision → checks → facts → document lines | the model's written reasoning and the quotes it chose |
| model calls per claim | one per document, one extraction, one per judged question, one narration | one |
| when a policy changes | edit the policy file | edit the prompt's policy text |

```bash
poetry run python -m claims_pipeline.benchmark.run <claims> --backend openai --out results/decomposed
poetry run python -m claims_pipeline.benchmark.run <claims> --backend openai --strategy e2e --out results/e2e
poetry run python -m claims_pipeline.benchmark.compare results/decomposed results/e2e
```

`compare` takes any number of runs: one column each, the whole claim-by-claim matrix, and the
summaries side by side wherever the runs differ.

Cloudflare's [Clef](https://huggingface.co/Cloudflare/clef) is a decision model: it returns a probability per
allowed answer and writes no text (set `CLAIMS_CLEF_BASE_URL`). Its main use here is as the **scorer**, which
decides nothing:

```bash
# every component on Claude (or Qwen); Clef puts a probability on each reading and each fact
poetry run python -m claims_pipeline.benchmark.run <claims> --backend anthropic --scorer clef --out results/claude-scored
```

- `score_document`: one request per document. Clef sees the document itself (the image, or the file's text),
  never the reader's transcription, and answers which of the policy's document types it is and whether it
  shows signs of alteration.
- `score_facts`: one request per claim, one question per extracted fact ("the value read is X; is it
  right?"), over the description and the documents, without the other extracted facts.
- The judge sees both next to the documents and facts it reads. A fact's confidence becomes Clef's "yes",
  a proof's confidence the weakest scored document type, and the decision's confidence is built from
  those as before (an approval is as strong as its weakest determining check, a denial as its strongest
  failing one). The verdicts and the decision itself do not change.
- A failed scoring call leaves the components' own confidences in place.

Clef can also stand in for a component or the whole pipeline:

```bash
# decomposed, but Clef answers every judged check: one choice question over yes / no / unknown;
# an answer below CLAIMS_CLEF_MIN_CONFIDENCE (0.6) is UNKNOWN. The check carries Clef's
# probabilities as its rationale and no quote.
poetry run python -m claims_pipeline.benchmark.run <claims> --backend openai --judge clef --out results/decomposed-clef
# the same over the mock claims: scripted reading and extraction, so only Clef's judging is tested
poetry run python -m claims_pipeline.benchmark.run mockclaims --backend scripted --judge clef --out results/mock-clef
# Clef end to end: one request per claim (policy, description, documents, at most 4 images),
# two choice questions (outcome, policy section). No reasoning and no payout.
poetry run python -m claims_pipeline.benchmark.run <claims> --strategy clef --out results/clef
```

(`--backend anthropic` for the Claude API; `CLAIMS_BACKEND` in `.env` sets the default.)

The comparison report puts accuracy, payout agreement, cost and latency side by side, counts the claims
each strategy got right that the other missed, and prints both summaries for every disagreement. Both
strategies fill the same result tree, so every e2e decision also opens in the UI and in the benchmark view.

## Setup

Requirements: Python 3.11+, [Poetry](https://python-poetry.org/) 2.x, Node 20+ (only to build the UI).

```bash
poetry install                                   # creates .venv in the project (see poetry.toml)
poetry run alembic upgrade head                  # the database (also done on first use)
(cd ui && npm install && npm run build)          # optional: the web UI
poetry run pytest                                # all offline
```

Run the API (serves the UI at http://localhost:8000/ui/ and OpenAPI docs at /docs):

```bash
poetry run uvicorn claims_pipeline.api.app:app --reload
```

Configuration is by environment variable (or a `.env` file), prefix `CLAIMS_`:

| variable | default | meaning |
|---|---|---|
| `CLAIMS_BACKEND` | `scripted` | `scripted` (replay from oracle files, no model), `anthropic` (Claude API) or `openai` (any OpenAI-compatible server) |
| `CLAIMS_MODEL` | `claude-opus-5-5` | model for every component unless overridden (with `openai`: the server's model name) |
| `CLAIMS_OPENAI_BASE_URL` | – | the server's `/v1` base URL for `openai` |
| `CLAIMS_OPENAI_API_KEY` | – | Bearer token for `openai`, when the server wants one |
| `CLAIMS_OPENAI_TIMEOUT_S` | `600` | per-call timeout for `openai` |
| `CLAIMS_OPENAI_DISABLE_THINKING` | `true` | ask Qwen3-style templates to skip the think phase for JSON replies |
| `CLAIMS_MODEL_<COMPONENT>` | – | per-component override: `READ_DOCUMENT`, `EXTRACT_FACTS`, `JUDGE`, `EXPLAIN`, `E2E` |
| `CLAIMS_EFFORT_<COMPONENT>` | medium/high/high/low/high | reasoning effort per component |
| `CLAIMS_FALLBACKS` | `true` | server-side refusal fallback on the Claude API; set `false` behind a gateway |
| `CLAIMS_DATABASE_URL` | `sqlite:///data/pipeline.db` | the store: claims, documents, runs, the whole proof chain, every model call (see "Data") |
| `CLAIMS_DEFAULT_POLICY` | `cfsr` | policy used when a submission names none |
| `ANTHROPIC_API_KEY` | – | required for the `anthropic` backend |

### Running against a local model

Any OpenAI-compatible server works (llama.cpp `llama-server`, vLLM, LM Studio, a LiteLLM gateway).
Structured output is requested as a JSON schema (`response_format`), images go as data URLs, so the
model and server must handle vision for the photographed documents.

```bash
cp .env.example .env                              # edit the URL and model name
poetry run python -m claims_pipeline.benchmark.run mockclaims --backend openai --out results/mock-local
poetry run uvicorn claims_pipeline.api.app:app     # the API and UI, now calling the local model
```

The first command is the cheapest end-to-end smoke test: the 22 mock claims through the real model,
scored against their expected outcomes, with per-component tokens and latency in `results.md`.

The server's JSON-schema grammar constrains only the answer tokens; the model never sees it. The client
therefore also appends the schema and an example of its shape to the system prompt. Without that, a
thinking model spends its budget guessing the format (the first blind run lost 12 of 122 calls that way).
Thinking is off by default. With `CLAIMS_OPENAI_DISABLE_THINKING=false`, only `extract_facts`, `judge`
and `e2e` think, sampled as Qwen recommends for thinking (temperature 0.6, top-p 0.95, top-k 20), since
greedy decoding makes thinking loop. Each component's thinking and token budget can be overridden (see
`.env.example`).

## Data

Everything lives in one database (`CLAIMS_DATABASE_URL`, SQLite by default; tables in
`claims_pipeline/store/models.py`, migrations in `migrations/`, plan in `docs/DATA_LAYER_PLAN.md`).
The tables use the policy file's words, so a decision can be traced row by row:
`decision` → `check_result` (`exclusion` / `prior_knowledge`) → `policy_check` (the question as
written in `cfsr.py`) → `check_evidence` → `fact` → `observation` → `reading` → `document`, and
from any judged check or observation to the `model_call` that produced it and its `prompt` versions.

- A claim and its documents are written once. Reprocessing makes a new `run`; finished runs are never edited.
- Every run records a `run_config`: the code version (git commit and a hash of the sources), the version of
  every prompt file in `claims_pipeline/prompts/`, and every setting. Same config id, same recipe.
- Prompts are files (`claims_pipeline/prompts/`), stored by hash; each model call lists the versions it used.
  `tests/golden/prompts.json` pins every render, so a prompt change shows up as a reviewed diff
  (regenerate with `python -m tests.prompt_renders`).
- A run can be replayed without a model from its recorded calls (`claims_pipeline/store/replay.py`): under
  today's code that shows what a rule change does to past decisions.
- The benchmark runner records each execution as a `batch` of runs (`--no-store` for files only).
  Older `results/<run>/` folders import with `python -m claims_pipeline.store.importer results/*`.

## API

| method | path | purpose |
|---|---|---|
| `POST` | `/claims` | submit a claim (multipart: `description`, `policy`, `metadata` JSON, `submitted_at`, `strategy` = `decomposed` or `e2e`, `files[]`). Returns 202 and a `claim_id`; processing runs in the background. Add `wait=true` to process inline. |
| `GET` | `/claims` | submitted claims, newest first |
| `GET` | `/claims/{id}` | status plus the full result tree of the latest run (`?run=<run_id>` for another) |
| `POST` | `/claims/{id}/runs` | process the claim again under today's code, prompts and settings: a new run, earlier runs kept |
| `GET` | `/claims/{id}/runs` | every run of the claim, with its config id and outcome |
| `GET` | `/claims/{id}/documents/{doc_id}` | the uploaded file behind a reading |
| `GET` | `/claims/{id}/calls` | every model call of the run, in full, with the prompt versions it was built from |
| `GET` | `/policies`, `/policies/{name}` | a policy's composition as data, plus its wording |
| `GET` | `/benchmarks`, `/benchmarks/{id}` | recorded benchmark executions, with per-claim results, documents and calls |
| `GET` | `/health` | backend and per-component model configuration |

Example:

```bash
curl -s -X POST localhost:8000/claims \
  -F "description=I was admitted to hospital two days before departure and had to cancel." \
  -F policy=cfsr -F submitted_at=2025-03-25 \
  -F files=@hospital-certificate.md -F files=@booking-confirmation.png
# {"claim_id":"b5e9a2641c32","status":"processing", ...}
curl -s localhost:8000/claims/b5e9a2641c32 | jq .result.decision
```

The response's `result` holds `documents`, `facts`, `exclusions`, `coverages` and `decision`.
`decision.determining` lists the check ids that forced the outcome; each check lists `facts_used`
and `document_ids`; each fact lists `observations` with `document_id`, `line` and `quote`.

## Decision logic

For each policy, in this order:

1. **Exclusions.** Each is a check with PASS meaning "does not apply". Any FAIL denies.
2. **Coverages.** Each has a match question, a proof rule over document types, optional extra checks and
   a payout formula. A coverage is *eligible* (all pass, payout computable), *not applicable* (match
   answered no), *ineligible* (it cannot pay: matched but a proof is missing or a check failed, or its
   required proof is missing whichever way its match would go) or *unresolved* (something needed is
   unknown).
3. **Outcome.** No coverage applicable: DENY (not a covered reason). An eligible coverage and no
   open exclusion: APPROVE, payouts of eligible coverages added. Nothing can pay and nothing is
   unresolved: DENY (an open exclusion cannot matter when nothing pays). Otherwise UNCERTAIN, with
   `unknowns` listing what a handler would need.
   A failed model call never loses the claim. A failed read leaves its document *unreadable*, a failed
   extraction leaves the facts unknown, a failed judgment is an UNKNOWN check, and a failed narration
   leaves the code summary. The failed calls head `unknowns`, and an approval reached while a read
   failed is referred instead.
4. **Confidence** is computed, not asked for: the weakest link among the checks that decided, which in
   turn comes from fact confidences and the model's confidence on judged questions.
5. **Trust.** Documents the reader flags as altered become *questionable* or *untrusted* before any
   check runs; an untrusted document cannot serve as proof, and facts that disagree across documents
   mark both documents questionable. The CFSR policy also asks the model directly whether any document
   looks altered or inconsistent, as a red flag (it refers, never denies).
6. **Review signals.** Besides its answer, every model component can say a person must look, and why:
   the reader when a claimant's document holds something unexpected for its kind (a booking that says
   it was already refunded), the extractor when a value reads two ways or had to be inferred, the judge
   when the evidence is there but the wording does not settle the question. Code carries the flags up
   the tree. A reader's flag always refers the claim; an extraction or judge flag refers it only when
   the decision rests on what was flagged (an approval on everything that passed and its payout facts,
   a denial on its determining checks). The referral names who flagged what and what the rules alone
   would have decided. Edge cases the policy wording does not settle are left to these flags, not
   written into the policy files.

## Benchmark

```bash
poetry run python -m claims_pipeline.benchmark.run mockclaims --out results/mock          # offline, scripted
poetry run python -m claims_pipeline.benchmark.run <claims folder> --policy cfsr --backend anthropic --out results/blind
```

(`poetry run claims-benchmark ...` is the same command as a console script.)

A claim folder holds `description.txt` and the documents; `expected.json` or `answer.json` gives the
expected outcome, optional `claim.json` gives the policy and submission date, and `oracle.json` holds
scripted model answers. The run writes one result JSON per claim plus `results.json` and `results.md`
with accuracy, a confusion matrix, per-outcome precision and recall, payout agreement, per-component
cost, and the evidence tree of every miss. Runs appear in the UI under *Benchmarks*.

![Benchmark view](docs/screenshots/benchmark.png)

`mockclaims/` is an adversarial set generated by `scripts/make_mockclaims.py`: one case per rule
(period boundaries, the 30-day reporting limit on both sides, missing proof, disagreeing dates, an
altered image, prior knowledge, a voluntary change, the connection formula with and without its inputs,
two coverages paying at once, a Spanish certificate, an unreadable attachment, an ambiguous weather
cancellation) plus four cases for the second policy. All 22 resolve as expected offline.

## Repository layout

```
claims_pipeline/
  domain/types.py       the result tree
  components/           facts, checks, coverage + proof rules, policy + decision rule, quantum, trust, explain, sources
  policies/             cfsr.py, gadget.py, texts/ (the policy wording)
  llm/                  backend interface, scripted backend, anthropic backend, per-component config, tracing
  pipeline.py           the five steps, same for every policy (decomposed strategy)
  e2e.py                the end-to-end strategy: one call, whole context
  api/                  FastAPI app + SQLite store
  benchmark/            loader, runner, report, compare
ui/                     React + Vite: claims, evidence view, submit, benchmarks, policies
mockclaims/             the adversarial offline set (generated)
docs/AUTHORING.md       how to compose a new policy
tests/                  components, decision rule, every mock claim end to end, API
```

## Assumptions and limits

* The benchmark's claims have no recorded submission date; the pipeline then relies on the
  description or documents for `claim_submitted`, and reports the 30-day check as unknown rather
  than guessing. The API uses the real submission time.
* Cancellation pays "up to the total bill": the formula pays the booking value when the proofs are in
  order, which is what a handler would do absent a stated lower amount.
* A new policy is a code change with review and deploy, not a runtime upload. That matches how
  insurers actually change policy terms, and keeps every rule reviewable in a diff.
* Image reading quality is the model's. Documents the reader cannot read are typed `unreadable` and
  turn missing proof into a referral instead of a denial.
