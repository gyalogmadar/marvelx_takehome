import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmt } from '../api'
import type { ClaimSummary, Outcome } from '../types'
import { Confidence, OutcomeBadge, StatusBadge } from '../components/Badges'

type Filter = 'all' | Outcome | 'processing' | 'failed'

export default function ClaimsList() {
  const [claims, setClaims] = useState<ClaimSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const navigate = useNavigate()

  useEffect(() => {
    let timer: number | undefined
    const load = () => api.listClaims().then((c) => {
      setClaims(c); setError(null)
      if (c.some((x) => x.status === 'processing')) timer = window.setTimeout(load, 1500)
    }).catch((e) => setError(String(e)))
    load()
    return () => window.clearTimeout(timer)
  }, [])

  const shown = (claims ?? []).filter((c) =>
    filter === 'all' ? true : filter === 'processing' || filter === 'failed' ? c.status === filter : c.decision === filter)
  const count = (f: Filter) => (claims ?? []).filter((c) => f === 'processing' || f === 'failed' ? c.status === f : c.decision === f).length

  return (
    <>
      <div className="page-head">
        <div><h1>Claims</h1><div className="sub">Every submitted claim with its decision. Open one to walk the evidence chain.</div></div>
        <Link to="/claims/new" className="btn primary right">Submit a claim</Link>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="filters">
          {(['all', 'APPROVE', 'DENY', 'UNCERTAIN', 'processing', 'failed'] as Filter[]).map((f) => (
            <button key={f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>
              {f === 'all' ? `all (${claims?.length ?? 0})` : f === 'UNCERTAIN' ? `refer (${count(f)})` : `${f.toLowerCase()} (${count(f)})`}
            </button>
          ))}
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <section className="panel">
        <div className="panel-body tight">
          {claims && shown.length === 0 ? (
            <div className="empty">
              {claims.length === 0 ? <>No claims yet. <Link to="/claims/new">Submit one</Link> or browse the <Link to="/benchmarks">benchmark runs</Link>.</> : 'Nothing matches this filter.'}
            </div>
          ) : (
            <table>
              <thead><tr><th>claim</th><th>policy</th><th>decision</th><th>confidence</th><th className="num">payout</th><th>submitted</th><th>summary</th></tr></thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.claim_id} className="clickable" onClick={() => navigate(`/claims/${c.claim_id}`)}>
                    <td className="mono">{c.claim_id}<div className="small faint">{c.files.length} file{c.files.length === 1 ? '' : 's'}</div></td>
                    <td>{c.policy}{c.strategy === 'e2e' && <div className="small faint">end-to-end</div>}{c.strategy === 'clef' && <div className="small faint">Clef end-to-end</div>}</td>
                    <td>{c.status === 'done' ? <OutcomeBadge outcome={c.decision} /> : <StatusBadge status={c.status} />}</td>
                    <td><Confidence value={c.confidence} /></td>
                    <td className="num">{fmt.money(c.payout, c.currency ?? 'EUR')}</td>
                    <td className="muted">{fmt.date(c.submitted_at)}</td>
                    <td className="muted">{c.summary ?? c.error ?? c.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </>
  )
}
