import type {
  BenchmarkResults, BenchmarkRunSummary, ClaimRecord, ClaimResult, ClaimSummary, Health, PolicyDescription, Reading,
} from './types'

// Results saved before the rename call the readings `documents`.
function withReadings(rec: ClaimRecord): ClaimRecord {
  const r = rec.result as (ClaimResult & { documents?: Reading[] }) | null
  if (r && !r.readings && r.documents) r.readings = r.documents
  return rec
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`)
  return r.json() as Promise<T>
}

/** Where a claim's result tree comes from: the live store, or a benchmark run on disk. */
export type ClaimSource = { kind: 'store'; claimId: string } | { kind: 'benchmark'; run: string; claimId: string }

export function claimUrl(src: ClaimSource): string {
  return src.kind === 'store'
    ? `/claims/${encodeURIComponent(src.claimId)}`
    : `/benchmarks/${encodeURIComponent(src.run)}/claims/${encodeURIComponent(src.claimId)}`
}

export function documentUrl(src: ClaimSource, docId: string): string {
  return `${claimUrl(src)}/documents/${encodeURIComponent(docId)}`
}

export const api = {
  health: () => get<Health>('/health'),
  listClaims: () => get<ClaimSummary[]>('/claims'),
  getClaim: async (src: ClaimSource) => withReadings(await get<ClaimRecord>(claimUrl(src))),
  submitClaim: async (form: FormData): Promise<{ claim_id: string }> => {
    const r = await fetch('/claims', { method: 'POST', body: form })
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
    return r.json()
  },
  policies: () => get<PolicyDescription[]>('/policies'),
  policy: (name: string) => get<PolicyDescription>(`/policies/${encodeURIComponent(name)}`),
  benchmarks: () => get<BenchmarkRunSummary[]>('/benchmarks'),
  benchmark: (run: string) => get<BenchmarkResults>(`/benchmarks/${encodeURIComponent(run)}`),
}

export const fmt = {
  money(v: number | null | undefined, currency = 'EUR'): string {
    if (v === null || v === undefined) return '–'
    return new Intl.NumberFormat('en-IE', { style: 'currency', currency, maximumFractionDigits: 2 }).format(v)
  },
  // "yes 82%, no 10%, unknown 8%": the most likely answers first
  probs(p: Record<string, number>, top = 3): string {
    return Object.entries(p).sort((a, b) => b[1] - a[1]).slice(0, top).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(', ')
  },
  pct(v: number | null | undefined): string {
    return v === null || v === undefined ? '–' : `${Math.round(v * 100)}%`
  },
  date(v: string | null | undefined): string {
    if (!v) return '–'
    const d = new Date(v)
    return isNaN(d.getTime()) ? v : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
  },
  dateTime(v: string | null | undefined): string {
    if (!v) return '–'
    const d = new Date(v)
    return isNaN(d.getTime()) ? v : d.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })
  },
  label(name: string): string {
    return name.replace(/[._]/g, ' ')
  },
  value(v: unknown): string {
    if (v === null || v === undefined) return 'unknown'
    if (typeof v === 'object') {
      const o = v as Record<string, unknown>
      if ('start' in o && 'end' in o) return `${o.start} → ${o.end}`
      return JSON.stringify(v)
    }
    if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2)
    return String(v)
  },
}
