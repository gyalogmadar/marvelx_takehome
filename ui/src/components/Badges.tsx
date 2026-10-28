import type { CoverageStatus, Outcome, Trust, Verdict } from '../types'

export function OutcomeBadge({ outcome, size }: { outcome: Outcome | null | undefined; size?: 'lg' }) {
  if (!outcome) return <span className="badge neutral">–</span>
  return <span className={`badge ${outcome} ${size ?? ''}`}>{outcome === 'UNCERTAIN' ? 'REFER' : outcome}</span>
}

export function StatusBadge({ status }: { status: 'processing' | 'done' | 'failed' }) {
  if (status === 'processing') return <span className="badge processing"><span className="spinner" /> processing</span>
  if (status === 'failed') return <span className="badge failed">failed</span>
  return null
}

export function VerdictIcon({ verdict, title }: { verdict: Verdict; title?: string }) {
  const glyph = verdict === 'pass' ? '✓' : verdict === 'fail' ? '✕' : '?'
  return <span className={`verdict ${verdict}`} title={title ?? verdict}>{glyph}</span>
}

const STATUS_LABEL: Record<CoverageStatus, string> = {
  eligible: 'eligible', ineligible: 'ineligible', unresolved: 'unresolved', not_applicable: 'not applicable',
}
export function CoverageStatusBadge({ status }: { status: CoverageStatus }) {
  return <span className={`badge ${status}`}>{STATUS_LABEL[status]}</span>
}

export function TrustBadge({ trust, reasons }: { trust: Trust; reasons?: string[] }) {
  if (trust === 'trusted') return null
  return <span className={`badge trust-${trust}`} title={reasons?.join('\n')}>{trust}</span>
}

export function Confidence({ value, showLabel = true }: { value: number | null | undefined; showLabel?: boolean }) {
  if (value === null || value === undefined) return <span className="muted">–</span>
  const pct = Math.round(value * 100)
  return (
    <span className="meter" title={`confidence ${pct}%`}>
      <span className={`bar ${value < 0.6 ? 'low' : ''}`}><i style={{ width: `${pct}%` }} /></span>
      {showLabel && <span className="val">{pct}%</span>}
    </span>
  )
}

export function KindTag({ kind }: { kind: 'mechanical' | 'judged' | 'proof' }) {
  const label = kind === 'judged' ? 'model' : kind === 'proof' ? 'proof' : 'rule'
  return <span className="kind-tag">{label}</span>
}
