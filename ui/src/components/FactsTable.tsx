import { useState } from 'react'
import { fmt } from '../api'
import type { Fact } from '../types'
import { Confidence } from './Badges'
import { useEvidence } from './EvidenceContext'

export function FactsTable({ facts }: { facts: Fact[] }) {
  const { showFact, docById } = useEvidence()
  const [open, setOpen] = useState(true)
  const known = facts.filter((f) => f.value !== null && f.value !== undefined)
  return (
    <section className="panel">
      <header className="panel-head" style={{ cursor: 'pointer' }} onClick={() => setOpen((o) => !o)}>
        <span className="muted" style={{ width: 14 }}>{open ? '▾' : '▸'}</span>
        <h2>Facts</h2>
        <span className="muted small right">{known.length} of {facts.length} known</span>
      </header>
      {open && (
        <div className="panel-body tight">
          <table className="facts-table">
            <tbody>
              {facts.map((f) => {
                const isKnown = f.value !== null && f.value !== undefined
                const best = [...f.observations].sort((a, b) => b.confidence - a.confidence)[0]
                const src = best ? docById(best.document_id) : undefined
                return (
                  <tr key={f.name} className={isKnown ? 'hover-row' : ''} onMouseEnter={() => isKnown && showFact(f.name)} onClick={() => isKnown && showFact(f.name)}>
                    <td>{fmt.label(f.name)}</td>
                    <td className={isKnown ? '' : 'faint'}>
                      <div className="row"><span className="val">{isKnown ? fmt.value(f.value) : 'unknown'}</span>{isKnown && <Confidence value={f.confidence} showLabel={false} />}</div>
                      {f.note && <div className="small muted">{f.note}</div>}
                      {src && <div className="small faint">{src.kind === 'description' ? 'claim description' : src.filename}{best.line ? `, line ${best.line}` : ''}{f.observations.length > 1 ? ` +${f.observations.length - 1}` : ''}</div>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
