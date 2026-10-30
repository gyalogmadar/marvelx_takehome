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

## Where this stands

There looks to be an idea worth pursuing here: **breaking a policy down into a workflow**. The policy
becomes a short file of typed facts, proof rules and checks; models only read documents and answer
narrow questions; code makes the decision and keeps the evidence for it. On the mock claims, that
workflow was more accurate than handing the same model the whole claim in one prompt, even on a local
model: Qwen 27B with Clef as the judge got 24 of 35 decisions and payouts right, against 20 of 35 for
Qwen end to end (recorded runs, before the latest round of fixes). The judge matters: Qwen judging
its own workflow got 17 of 35, 21 once the decision-logic fixes were replayed over the same answers.

The separation of concerns is brittle. The boundary between "the policy" and "the machinery" holds in
the code, but it does not hold by itself: while iterating against failing cases, the coding agent
repeatedly tried to hard-code rules for individual edge cases into the policy files. The current answer
is the review signal (every model component can say "a person must look at this"), so cases the policy
wording does not settle go to a human instead of into a new rule. Where that line belongs is a product
question, not a code question.

Settling it needs more product knowledge: more than one real policy, and a closer look at how claim
adjusters actually think and work (what they check first, what makes them refer, what they consider
evidence). For a showcase it works.

## Reading the code

The suggested path below follows a claim from the policy it is judged against to the screen it is
shown on. Each step is one package with one job, and each only depends on the ones before it.

**1. Start with a policy.** Open `claims_pipeline/policies/texts/cfsr.md`, the policy wording, next to
`claims_pipeline/policies/cfsr.py`, the same policy as code. Each clause is one line: the facts to find
(`Date("incident_date", ...)`), each covered section (`Coverage`: a question that says whether it
applies, the proof it requires, its checks, its payout formula), the exclusions and the red flags.
`gadget.py` is a second, deliberately different policy built from the same parts. Anything only one
policy needs lives in that policy's file and nowhere else.

**2. The vocabulary a policy is written in: `components/`.** `facts.py` declares the fact types and
how several readings of one fact merge. `checks.py` holds the mechanical checks (a date inside a period,
days between two dates, a document present) and `judged`, the one check that asks a model.
`coverage.py` evaluates one covered section, using `quantum.py` for the payout formula. `policy.py`
holds the one fixed decision rule, and the rule that sends a claim to a person when a component flags
something the decision rests on. `sources.py` turns the uploaded files into readings, `trust.py`
marks documents that look altered, `scoring.py` attaches the scorer's probabilities, and `explain.py`
writes the narrative. None of it knows any particular policy.

**3. What comes out: `domain/`.** `claim.py` is a claim as submitted. `types.py` is the result tree:
decision, then the checks that decided it, then the facts they used, then the observations of each
fact (document, line, quote), then the readings. The API returns it, the UI draws it, the benchmark
scores it and the store keeps it.

**4. The workflow: `pipeline.py`.** The same steps for every policy: read every document, extract the
facts, score them, assess trust, run the checks, decide, narrate. About a hundred lines. Read it once
and the rest falls into place.

**5. Where a model comes in: `llm/` and `prompts/`.** The interface between the workflow and any model
is `llm/base.py` and `llm/types.py`: four components (read a document, extract facts, judge one
question, explain), each with a typed request and answer, each able to raise a review flag.
`chat_backend.py` implements them on a chat model; `anthropic_client.py` and `openai_client.py` are the
two clients it runs on (Claude, or any OpenAI-compatible server such as llama.cpp); `render.py` is how
evidence is written into a model's input. `clef.py` is Cloudflare's Clef, a decision model that can
stand in for the judge or score what the others concluded. `scripted.py` answers from an oracle file,
so the whole system runs offline and deterministically. `clients.py` picks a client and routes single
components to Clef. `trace.py`, `calllog.py` and `pricing.py` record every call, in full, with its
cost. The words the models are told are files in `claims_pipeline/prompts/`, versioned by hash and
pinned by `tests/golden/prompts.json`.

**6. Choosing how to process a claim: `strategies.py`.** The one place that decides what runs:
*decomposed* (the workflow above), *e2e* (`e2e.py`: one chat-model call with the whole claim) or
*clef* (`clef_e2e.py`: one Clef request). The API, the benchmark runner and replay all ask here. All
three strategies fill the same result tree, so they compare claim by claim.

**7. Keeping every run: `store/`.** Persistence and nothing else. `models.py` is the schema, named in
the policy's own words (a `check_result` points at the `policy_check` that asked it), migrated by
Alembic (`migrations/`). `repository.py` saves a result tree and loads it back exactly. `runs.py` is
the lifecycle of a run: open it, record its model calls as they happen (`calls.py`), save the tree or
the failure. `runconfig.py` hashes what a run was made with (code version, prompt versions, settings),
so two runs with the same config id were made the same way. `queries.py` is what the API reads.

**8. Measuring: `benchmark/` and `replay.py`.** `benchmark/run.py` runs a folder of claims and records
the execution as a batch; `loader.py` reads a claim folder, `report.py` scores, `views.py` reads
recorded batches back with their scores, `compare.py` lines up any number of runs, and `importer.py`
loads result folders written before the store existed. `replay.py` re-runs a recorded run with no
model, from the answers its calls returned: under today's code it shows what a rule change does to
past decisions. `mockclaims/`, generated by `scripts/make_mockclaims.py`, is the adversarial test set.

**9. Where people meet it: `api/` and `ui/`.** `api/app.py` is the FastAPI surface (submit, look up,
reprocess, the evidence behind a reading, benchmarks); it only reads and writes through the store.
`ui/` is React: the claim list, the evidence view (`ClaimDetail.tsx`: decision, checks, facts and the
document lines they came from, lit up on hover), submission, benchmarks and the policy view.

**How it ties together.** Dependencies point one way:

```
domain, prompts, settings
  -> components -> policies
  -> llm                      (the model interface, clients, Clef, the call log)
  -> pipeline, e2e, clef_e2e  (the three ways to process a claim)
  -> strategies               (which one runs, on what)
  -> store                    (keeping it)
  -> benchmark, replay        (measuring it)
  -> api, ui                  (showing it)
```

Where to change what:

| to change | edit |
|---|---|
| what a policy covers or excludes | its file in `policies/` (and its wording in `policies/texts/`) |
| a kind of fact, check or proof rule every policy can use | `components/` |
| what a model is told | the file in `claims_pipeline/prompts/`, then `python -m tests.prompt_renders` and review the diff |
| which model runs which component, and how | settings (`claims_pipeline/settings.py`, `.env`) |
| when a claim goes to a person | `components/policy.py` (`decide`, `_review`) |
| what is kept about a run | `store/models.py`, plus an Alembic migration |

## Strategies, side by side

The repository runs the same claims in different ways and compares them. The two main ones:

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
cancellation) plus four cases for the second policy, and the `b` cases shaped like the real claims
(names written differently, WebP certificates, a booking that says it was already refunded). All 36
resolve as expected offline.

## Assumptions and limits

* The benchmark's claims have no recorded submission date. The intake system's `Current date is:`
  stamp at the top of a document is taken as the submission date (every model is told so); without it
  the 30-day check is unknown rather than guessed. The API uses the real submission time.
* Cancellation pays "up to the total bill": the formula pays the booking value when the proofs are in
  order, which is what a handler would do absent a stated lower amount.
* A new policy is a code change with review and deploy, not a runtime upload. That matches how
  insurers actually change policy terms, and keeps every rule reviewable in a diff.
* Image reading quality is the model's. Documents the reader cannot read are typed `unreadable` and
  turn missing proof into a referral instead of a denial.
