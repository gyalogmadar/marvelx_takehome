import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, type ClaimSource } from '../api'
import type { ClaimRecord } from '../types'
import { EvidenceProvider } from '../components/EvidenceContext'
import { DecisionBanner } from '../components/DecisionBanner'
import { CheckRow } from '../components/CheckRow'
import { CoveragePanel } from '../components/CoveragePanel'
import { DocumentList, DocumentViewer } from '../components/Documents'
import { FactsTable } from '../components/FactsTable'
import { CallsTable } from '../components/CallsTable'
import { StatusBadge } from '../components/Badges'

export default function ClaimDetail({ source }: { source: ClaimSource }) {
  const [rec, setRec] = useState<ClaimRecord | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let timer: number | undefined
    let cancelled = false
    const load = () => api.getClaim(source).then((r) => {
      if (cancelled) return
      setRec(r); setError(null)
      if (r.status === 'processing') timer = window.setTimeout(load, 1500)
    }).catch((e) => !cancelled && setError(String(e)))
    load()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [source.kind, source.claimId, source.kind === 'benchmark' ? source.run : ''])

  const determining = useMemo(() => new Set(rec?.result?.decision.determining ?? []), [rec])
  const back = source.kind === 'store' ? '/' : `/benchmarks/${source.run}`

  if (error) return <div className="error">{error}</div>
  if (!rec) return <div className="empty"><span className="spinner" /> loading</div>

  const result = rec.result
  return (
    <>
      <div className="page-head">
        <div>
          <div className="small"><Link to={back}>← {source.kind === 'store' ? 'all claims' : `benchmark ${source.run}`}</Link></div>
          <h1 className="row">{rec.claim_id} <StatusBadge status={rec.status} /></h1>
          <div className="sub">
            policy <strong>{rec.policy ?? result?.policy}</strong>
            {result?.strategy === 'e2e' && <> · <span className="pill" title="one model call with the whole context; no facts or checks">end-to-end</span></>}
            {rec.submitted_at && <> · submitted {fmt.date(rec.submitted_at)}</>}
            {result?.processed_at && <> · processed {fmt.dateTime(result.processed_at)}</>}
          </div>
        </div>
      </div>

      {rec.status === 'failed' && <div className="error">Processing failed: {rec.error}</div>}
      {rec.status === 'processing' && <div className="notice row"><span className="spinner" /> Reading documents, extracting facts and running checks…</div>}

      {result && (
        <EvidenceProvider source={source} documents={result.documents} facts={result.facts}>
          <div className="detail">
            <aside className="stack">
              <section className="panel">
                <header className="panel-head"><h2>Documents</h2><span className="muted small right">{result.documents.length}</span></header>
                <div className="panel-body tight"><DocumentList /></div>
              </section>
              <FactsTable facts={result.facts} />
            </aside>

            <div className="stack">
              <DecisionBanner decision={result.decision} expected={rec.expected} expectedPayout={rec.expected_payout} note={rec.note} />
              {result.strategy === 'e2e' ? (
                <section className="panel"><div className="panel-body muted">
                  End-to-end strategy: the model received the policy, the description and every document in one prompt and returned this decision directly.
                  There are no extracted facts or rule checks to show; the written explanation above is the whole reasoning.
                </div></section>
              ) : (
              <section className="panel">
                <header className="panel-head"><h2>Exclusions</h2><span className="muted small">pass means the exclusion does not apply</span></header>
                <div className="panel-body tight">
                  {result.exclusions.map((e) => <CheckRow key={e.id} check={e} determining={determining.has(e.id)} />)}
                </div>
              </section>
              )}
              {result.coverages.map((c) => <CoveragePanel key={c.name} coverage={c} determining={determining} currency={result.decision.currency} />)}
              <CallsTable calls={result.calls} />
            </div>

            <div className="col-right"><DocumentViewer /></div>
          </div>
        </EvidenceProvider>
      )}
    </>
  )
}
