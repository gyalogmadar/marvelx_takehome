import { fmt } from '../api'
import { useEvidence } from './EvidenceContext'

export function FactChip({ name }: { name: string }) {
  const { facts, showFact } = useEvidence()
  const fact = facts[name]
  if (!fact) return <span className="chip unknown"><span className="k">{fmt.label(name)}</span></span>
  const known = fact.value !== null && fact.value !== undefined
  const conf = fact.confidence
  const sources = fact.observations.map((o) => `${o.document_id}${o.line ? ` line ${o.line}` : ''}: “${o.quote}”`).join('\n')
  const title = known ? `${fmt.label(name)} = ${fmt.value(fact.value)} (confidence ${Math.round(conf * 100)}%)\n${sources}${fact.note ? `\n${fact.note}` : ''}` : `${fmt.label(name)} is unknown`
  return (
    <button type="button" className={`chip ${known ? '' : 'unknown'}`} title={title}
      onMouseEnter={() => known && showFact(name)} onClick={() => known && showFact(name)}>
      {known && <span className={`conf ${conf < 0.6 ? 'low' : conf < 0.85 ? 'mid' : ''}`} />}
      <span className="k">{fmt.label(name)}</span>
      <span className="v">{known ? fmt.value(fact.value) : 'unknown'}</span>
    </button>
  )
}

export function DocChip({ id }: { id: string }) {
  const { docById, selectDoc } = useEvidence()
  const doc = docById(id)
  return (
    <button type="button" className="chip doc" title={doc ? `${doc.doc_type} · ${doc.kind}` : id} onClick={() => selectDoc(id)}>
      {doc?.filename ?? id}
    </button>
  )
}
