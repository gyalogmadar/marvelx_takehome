# Composing a policy

A policy is one Python file in `claims_pipeline/policies/`. It is written against the policy document
and should be readable next to it, clause by clause. This guide is for a person; it also works as
instructions for a coding agent drafting a first version for a human to review.

## 1. Read the wording and list the clauses

Three kinds of clause exist in any policy:

* **Coverages** (insuring clauses): "if X happens you may be compensated", what proof they demand,
  and how they pay.
* **Exclusions**: "not covered when ...". Any one of them denies.
* **Conditions**: duties of the claimant, like reporting deadlines. Treat them as exclusions.

Write them down as a table before writing code. For CFSR that table has three coverages and four
exclusions, and the file has exactly those seven entries.

## 2. Declare the facts

Only declare facts a check or a formula needs. Each has a type and a hint the extractor sees:

```python
facts=[
    Date("incident_date", "the date the covered event happened: ..."),
    Period("policy_period", "the policy start and end dates"),
    Money("booking_value", "the total amount paid for the booking, in EUR"),
    Int("passengers", "number of passengers on the booking"),
]
```

Types: `Date`, `Period` (start/end), `Money`, `Int`, `Text`. A `Period` fact can be referenced as
`policy_period.start` and `policy_period.end`. `claim_submitted` is filled in automatically from the
submission time when the policy declares it and the extractor found nothing.

The hint matters: the extractor is told to normalise dates to ISO and to quote the line it read.
Say what counts ("illness onset or admission") and what to do when something is implicit ("default 1
if clearly a single traveller").

## 3. Name the document types

`doc_types` is the vocabulary the reader classifies documents into. Keep it small, keep `other` and
`unreadable`, and describe each type in words a reader can match against a scanned page.

## 4. Write each coverage

```python
Coverage(
    name="missed_connection",
    title="Missed Departure or Missed Connection",
    matches=judged("missed_departure", "Did the claimant miss a scheduled departure or connection "
                                        "because of an external covered cause, such as a traffic accident en route?"),
    proofs=all_of(any_of("incident_report", "police_report", "medical_report"), "booking_confirmation"),
    checks=[agrees_across_documents("incident_date")],
    payout=formula("0.5 * booking_value / passengers * affected"),
)
```

* `matches` is a judged yes/no question. Quote the wording's own examples in it. "Yes" means this
  coverage applies; "no" means not this one; "unknown" refers the claim.
* `proofs` is a rule over document types with `any_of` / `all_of`, nested as needed. An untrusted
  document does not count; an `unreadable` document turns a missing proof into unknown.
* `checks` are extra conditions that apply only inside this coverage.
* `payout` is an arithmetic expression over `Money` and `Int` facts with `min`, `max`, `round`. It is
  shown verbatim in the UI, so write it the way the wording states it.

## 5. Write the exclusions

Each exclusion is a check whose PASS means "does not apply":

```python
exclusions=[
    within_period("incident_date", "policy_period"),
    days_between_at_most("policy_period.end", "claim_submitted", 30, id="late_reporting",
                         description="claim submitted within 30 days of the policy ending"),
    judged("prior_knowledge", "Did the claimant know about the incident ... before purchasing the policy?",
           expect="no", facts=("incident_date", "purchase_date")),
]
```

Mechanical checks available: `within_period(date, period)`, `before(a, b)`,
`days_between_at_most(start, end, n)`, `agrees_across_documents(fact)`, `document_present(*types)`.
For a judged exclusion, ask the question the way the wording poses it and set `expect="no"`.
`facts=(...)` lists the facts a reader should see next to the answer; it does not change the answer.

"Not a covered reason" needs no exclusion: it is what happens when no coverage matches.

## 6. Give every check a stable id

Mechanical checks get an id from their parameters; judged checks need an explicit one. Ids appear in
the UI, in `decision.determining`, and as keys in oracle files, so treat them like API names.

## 7. Register and test

Add the policy to `POLICIES` in `claims_pipeline/policies/__init__.py`. Then write three to five
mock claims for it in `scripts/make_mockclaims.py`: a clean approval, one case per exclusion, and
one referral. Run the generator and the tests:

```bash
poetry run python scripts/make_mockclaims.py
poetry run pytest -q
poetry run python -m claims_pipeline.benchmark.run mockclaims --out results/mock
```

The scripted backend is strict: it fails loudly when the pipeline asks a judged question the oracle
does not answer, which is how you learn what your composition actually asks.

## What to keep out of the library

If a check is needed by one policy only, write it in that policy's file as a small class with `id`,
`description` and `run(ctx) -> CheckResult`. The library should grow only when a second policy needs
the same thing.
