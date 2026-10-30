import { useState } from 'react'
import { fmt } from '../api'
import type { Decision, Outcome } from '../types'
import { Confidence, OutcomeBadge } from './Badges'

export function DecisionBanner({ decision, expected, expectedPayout, note }:
  { decision: Decision; expected?: Outcome | null; expectedPayout?: number | null; note?: string }) {
  const [showExplain, setShowExplain] = useState(decision.determining.length === 0 && !!decision.explanation)
  const expectedMatches = expected ? expected === decision.outcome : null
  return (
    <section className={`panel decision ${decision.outcome}`}>
      <div className="panel-body">
        <div className="row wrap">
          <OutcomeBadge outcome={decision.outcome} size="lg" />
          <Confidence value={decision.confidence} />
          {decision.coverages_applied.length > 0 && (
            <span className="muted">under {decision.coverages_applied.map(fmt.label).join(', ')}</span>
          )}
          {expected !== undefined && expected !== null && (
            <span className="compare right">
              <span className="muted small">expected</span>
              <OutcomeBadge outcome={expected} />
              <span className={`verdict ${expectedMatches ? 'pass' : 'fail'}`}>{expectedMatches ? '✓' : '✕'}</span>
            </span>
          )}
          {decision.payout !== null && (
            <span className={`payout ${expected ? '' : 'right'}`}>{fmt.money(decision.payout, decision.currency)}
              {expectedPayout !== undefined && expectedPayout !== null && Math.abs(expectedPayout - decision.payout) > 0.01 && (
                <span className="muted small"> (expected {fmt.money(expectedPayout, decision.currency)})</span>
              )}
            </span>
          )}
        </div>
        <p className="summary">{decision.summary}</p>
        {note && <p className="muted small" style={{ marginTop: 6 }}>Test case: {note}</p>}
        {(decision.reviews ?? []).length > 0 && (
          <div className="reviews">
            <strong>Flagged for a closer look</strong>
            <ul>{decision.reviews!.map((r, i) => {
              const [who, what] = r.split(': ', 2)
              return <li key={i}><span className="pill">{who}</span> {what ?? r}</li>
            })}</ul>
          </div>
        )}
        {decision.unknowns.length > 0 && (
          <div className="unknowns">
            <strong>What would resolve this</strong>
            <ul>{decision.unknowns.map((u, i) => <li key={i}>{u}</li>)}</ul>
          </div>
        )}
        {decision.determining.length > 0 && (
          <div className="determ">
            <span className="muted small">decided by</span>
            {decision.determining.map((id) => <a key={id} href={`#check-${id}`}>{id}</a>)}
          </div>
        )}
        {decision.explanation && (
          <div style={{ marginTop: 10 }}>
            <button type="button" className="btn sm" onClick={() => setShowExplain((s) => !s)}>
              {showExplain ? 'Hide' : 'Show'} written explanation
            </button>
            {showExplain && <div className="explain">{decision.explanation}</div>}
          </div>
        )}
      </div>
    </section>
  )
}
