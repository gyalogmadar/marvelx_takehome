import { useState } from 'react'
import { fmt } from '../api'
import type { CoverageResult } from '../types'
import { CoverageStatusBadge } from './Badges'
import { CheckRow } from './CheckRow'

export function CoveragePanel({ coverage, determining, currency }: { coverage: CoverageResult; determining: Set<string>; currency: string }) {
  const [open, setOpen] = useState(coverage.status !== 'not_applicable')
  return (
    <section className="panel coverage">
      <header className="panel-head" onClick={() => setOpen((o) => !o)}>
        <span className="muted" style={{ width: 14 }}>{open ? '▾' : '▸'}</span>
        <h2>{coverage.title}</h2>
        <CoverageStatusBadge status={coverage.status} />
        <span className="right row">
          {coverage.payout_formula && <span className="formula" title="payout formula">{coverage.payout_formula}</span>}
          {coverage.payout !== null && <span className="amount">{fmt.money(coverage.payout, currency)}</span>}
        </span>
      </header>
      {open && (
        <div className="panel-body tight">
          <CheckRow check={coverage.match} determining={determining.has(coverage.match.id)} />
          <CheckRow check={coverage.proofs} determining={determining.has(coverage.proofs.id)} collapsed={coverage.status === 'not_applicable'} />
          {coverage.checks.map((c) => <CheckRow key={c.id} check={c} determining={determining.has(c.id)} />)}
          {coverage.payout_note && coverage.status !== 'not_applicable' && (
            <div className="check"><span /><div className="muted">{coverage.payout_note}</div></div>
          )}
        </div>
      )}
    </section>
  )
}
