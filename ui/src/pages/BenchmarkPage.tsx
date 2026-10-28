import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmt } from '../api'
import type { BenchmarkResults, BenchmarkRunSummary, Outcome } from '../types'
import { Confidence, OutcomeBadge } from '../components/Badges'

const OUTCOMES: Outcome[] = ['APPROVE', 'DENY', 'UNCERTAIN']

export default function BenchmarkPage() {
  const { run } = useParams()
  const [runs, setRuns] = useState<BenchmarkRunSummary[]>([])
  const [data, setData] = useState<BenchmarkResults | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [missesOnly, setMissesOnly] = useState(false)
  const navigate = useNavigate()

  useEffect(() => { api.benchmarks().then(setRuns).catch((e) => setError(String(e))) }, [])
  useEffect(() => {
    const target = run ?? runs[0]?.name
    if (target) api.benchmark(target).then(setData).catch((e) => setError(String(e)))
  }, [run, runs])

  const m = data?.metrics
  const rows = (data?.claims ?? []).filter((r) => !missesOnly || (r.expected && !r.match))
  return (
    <>
      <div className="page-head"><div><h1>Benchmarks</h1><div className="sub">Each run processes a folder of claims and scores it against the expected answers. Open any row to see its evidence tree.</div></div></div>
      {error && <div className="error">{error}</div>}
      {runs.length === 0 && !error && <div className="empty">No benchmark runs under <code>results/</code> yet. Run <code>python -m claims_pipeline.benchmark.run mockclaims --out results/mock</code>.</div>}
      <div className="runs" style={{ marginBottom: 16 }}>
        {runs.map((r) => (
          <Link key={r.name} to={`/benchmarks/${r.name}`} className={`run-card ${(run ?? runs[0]?.name) === r.name ? 'on' : ''}`}>
            <div className="row"><strong>{r.title}</strong><span className="pill right">{r.strategy ?? 'decomposed'} · {r.backend}</span></div>
            <div className="acc">{fmt.pct(r.accuracy)}</div>
            <div className="muted small">{r.correct}/{r.scored} correct · ${r.total_cost_usd?.toFixed(2)} · {fmt.dateTime(r.run_at)}</div>
          </Link>
        ))}
      </div>
      {data && m && (
        <div className="stack">
          <div className="tiles">
            <div className="tile"><div className="k">Decision accuracy</div><div className="v">{fmt.pct(m.accuracy)}</div><div className="s">{m.correct} of {m.scored} scored claims</div></div>
            <div className="tile"><div className="k">Payouts exact</div><div className="v">{m.payout.exact}/{m.payout.scored}</div><div className="s">where an amount was expected</div></div>
            <div className="tile"><div className="k">Model cost</div><div className="v">${m.total_cost_usd.toFixed(2)}</div><div className="s">{Object.values(m.components).reduce((s, c) => s + c.calls, 0)} calls</div></div>
            <div className="tile"><div className="k">Errors</div><div className="v">{m.unprocessed}</div><div className="s">claims that failed to process</div></div>
          </div>
          <div className="policy-grid">
            <section className="panel">
              <header className="panel-head"><h2>Confusion matrix</h2><span className="muted small">rows expected, columns actual</span></header>
              <div className="panel-body tight">
                <table className="matrix">
                  <thead><tr><th style={{ textAlign: 'left' }}>expected ↓</th>{OUTCOMES.map((o) => <th key={o}>{o === 'UNCERTAIN' ? 'REFER' : o}</th>)}<th>recall</th></tr></thead>
                  <tbody>
                    {OUTCOMES.map((e) => (
                      <tr key={e}>
                        <td style={{ textAlign: 'left' }}><OutcomeBadge outcome={e} /></td>
                        {OUTCOMES.map((a) => { const n = m.confusion[e]?.[a] ?? 0; return <td key={a} className={`${e === a ? 'diag' : 'off'} ${n === 0 ? 'zero' : ''}`}>{n}</td> })}
                        <td className="muted">{m.per_outcome[e]?.recall === null ? '–' : fmt.pct(m.per_outcome[e]?.recall)}</td>
                      </tr>
                    ))}
                    <tr><td style={{ textAlign: 'left' }} className="muted">precision</td>{OUTCOMES.map((a) => <td key={a} className="muted">{m.per_outcome[a]?.precision === null ? '–' : fmt.pct(m.per_outcome[a]?.precision)}</td>)}<td /></tr>
                  </tbody>
                </table>
              </div>
            </section>
            <section className="panel">
              <header className="panel-head"><h2>Cost per component</h2></header>
              <div className="panel-body tight">
                <table>
                  <thead><tr><th>component</th><th className="num">calls</th><th className="num">input</th><th className="num">cached</th><th className="num">output</th><th className="num">cost</th><th className="num">avg ms</th></tr></thead>
                  <tbody>
                    {Object.entries(m.components).map(([k, c]) => (
                      <tr key={k}><td>{k.replace(/_/g, ' ')}</td><td className="num">{c.calls}</td><td className="num">{c.input_tokens}</td><td className="num">{c.cache_read_tokens}</td><td className="num">{c.output_tokens}</td><td className="num">${c.cost_usd.toFixed(4)}</td><td className="num">{c.calls ? Math.round(c.latency_ms / c.calls) : 0}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
          <section className="panel">
            <header className="panel-head">
              <h2>Claims</h2>
              <span className="muted small">{rows.length} shown</span>
              <label className="right row small muted"><input type="checkbox" checked={missesOnly} onChange={(e) => setMissesOnly(e.target.checked)} /> misses only</label>
            </header>
            <div className="panel-body tight">
              <table>
                <thead><tr><th>claim</th><th>expected</th><th>actual</th><th className="num">expected payout</th><th className="num">actual payout</th><th>confidence</th><th>summary</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.claim_id} className={`clickable ${r.expected && !r.match ? 'miss' : ''}`} onClick={() => !r.error && navigate(`/benchmarks/${data.name}/claims/${r.claim_id}`)}>
                      <td><div className="mono">{r.claim_id}</div>{r.note && <div className="small faint">{r.note}</div>}</td>
                      <td><OutcomeBadge outcome={r.expected} /></td>
                      <td>{r.error ? <span className="badge failed">error</span> : <OutcomeBadge outcome={r.actual} />}</td>
                      <td className="num">{fmt.money(r.expected_payout)}</td>
                      <td className="num">{fmt.money(r.actual_payout)}</td>
                      <td><Confidence value={r.confidence} /></td>
                      <td className="muted">{r.error ?? r.summary}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </>
  )
}
