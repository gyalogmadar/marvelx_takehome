// Mirrors claims_pipeline/domain/types.py. The UI renders this tree and nothing else.

export type Verdict = 'pass' | 'fail' | 'unknown'
export type Outcome = 'APPROVE' | 'DENY' | 'UNCERTAIN'
export type Trust = 'trusted' | 'questionable' | 'untrusted'
export type DocumentKind = 'description' | 'metadata' | 'markdown' | 'text' | 'image'
export type CoverageStatus = 'eligible' | 'not_applicable' | 'ineligible' | 'unresolved'

export interface Reading {
  id: string
  filename: string
  kind: DocumentKind
  doc_type: string
  language: string
  issuer: string | null
  lines: string[]
  alteration_signs: string[]
  trust: Trust
  trust_reasons: string[]
  media_type: string | null
  summary: string | null
  scores?: Record<string, Record<string, number>>  // independent scorer: per question, a probability per answer
  review?: string | null  // the reader: something unexpected in the document; always sends the claim to a person
}

export interface Observation {
  document_id: string
  line: number | null
  quote: string
  value: unknown
  confidence: number
}

export interface Fact {
  name: string
  type: string
  value: unknown
  confidence: number
  observations: Observation[]
  note: string | null
  scores?: Record<string, number> | null  // independent scorer: yes / no / unknown; confidence is its "yes"
  review?: string | null  // carried up from the extractor: ambiguous or inferred
}

export interface CheckResult {
  id: string
  kind: 'mechanical' | 'judged' | 'proof'
  description: string
  verdict: Verdict
  rationale: string
  facts_used: string[]
  document_ids: string[]
  quote: string | null
  confidence: number
  review?: string | null  // the judge: the evidence is there, the wording does not settle it
}

export interface CoverageResult {
  name: string
  title: string
  match: CheckResult
  proofs: CheckResult
  checks: CheckResult[]
  status: CoverageStatus
  payout: number | null
  payout_formula: string | null
  payout_note: string | null
}

export interface Decision {
  outcome: Outcome
  confidence: number
  summary: string
  determining: string[]
  coverages_applied: string[]
  payout: number | null
  currency: string
  unknowns: string[]
  reviews?: string[]  // why a person must look at this claim, and which component said so
  explanation: string | null
}

export interface ModelCall {
  component: string
  model: string
  call_id?: string | null
  reasoning_tokens?: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  latency_ms: number
  cost_usd: number
  ok: boolean
  error: string | null
}

export interface ClaimResult {
  claim_id: string
  policy: string
  strategy?: 'decomposed' | 'e2e' | 'clef'
  description: string
  submitted_at: string | null
  processed_at: string
  readings: Reading[]  // each document as it was read in this run
  facts: Fact[]
  exclusions: CheckResult[]
  red_flags?: CheckResult[]
  coverages: CoverageResult[]
  decision: Decision
  calls: ModelCall[]
}

export interface ClaimRecord {
  claim_id: string
  status: 'processing' | 'done' | 'failed'
  policy: string | null
  description?: string
  submitted_at: string | null
  files: string[]
  result: ClaimResult | null
  error: string | null
  // present for benchmark claims
  expected?: Outcome | null
  expected_payout?: number | null
  note?: string
}

export interface ClaimSummary {
  claim_id: string
  policy: string
  strategy?: string | null
  status: 'processing' | 'done' | 'failed'
  submitted_at: string | null
  created_at: string
  files: string[]
  decision: Outcome | null
  confidence: number | null
  payout: number | null
  currency: string | null
  summary: string | null
  coverages_applied: string[]
  error: string | null
  description: string
}

export interface PolicyDescription {
  name: string
  title: string
  currency: string
  facts: { name: string; type: string; hint: string }[]
  doc_types: Record<string, string>
  coverages: {
    name: string
    title: string
    matches: { id: string; description: string }
    proofs: string
    checks: { id: string; description: string }[]
    payout: string | null
  }[]
  exclusions: { id: string; description: string }[]
  red_flags?: { id: string; description: string }[]
  text?: string
}

export interface BenchmarkRunSummary {
  name: string
  title: string
  backend: string | null
  strategy?: string
  run_at: string | null
  claims: number
  scored: number
  correct: number
  accuracy: number | null
  total_cost_usd: number
}

export interface BenchmarkRow {
  claim_id: string
  policy: string
  note: string
  expected: Outcome | null
  expected_payout: number | null
  actual: Outcome | null
  actual_payout: number | null
  match: boolean
  confidence: number | null
  summary: string | null
  error?: string
}

export interface ComponentStats {
  calls: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cost_usd: number
  latency_ms: number
  errors: number
}

export interface BenchmarkResults {
  title: string
  name: string
  backend: string
  strategy?: string
  claims_root: string
  run_at: string
  metrics: {
    claims: number
    scored: number
    correct: number
    accuracy: number | null
    confusion: Record<Outcome, Record<Outcome, number>>
    per_outcome: Record<Outcome, { expected: number; predicted: number; precision: number | null; recall: number | null }>
    payout: { scored: number; exact: number }
    components: Record<string, ComponentStats>
    total_cost_usd: number
    unprocessed: number
  }
  claims: BenchmarkRow[]
}

export interface Health {
  status: string
  backend: string
  components: Record<string, { model: string; effort: string }>
  policies: string[]
}
