import { useEffect, useRef } from 'react'
import type { Document } from '../types'
import { TrustBadge } from './Badges'
import { useEvidence } from './EvidenceContext'

const ICON: Record<Document['kind'], string> = { description: '✎', metadata: '⚙', markdown: '▤', text: '▤', image: '▨' }

export function DocumentList() {
  const { documents, selectedDoc, selectDoc } = useEvidence()
  return (
    <div className="doclist">
      {documents.map((d) => (
        <button type="button" key={d.id} className={`doc ${selectedDoc === d.id ? 'selected' : ''}`} onClick={() => selectDoc(d.id)}>
          <span className="icon" aria-hidden>{ICON[d.kind]}</span>
          <span className="grow">
            <div className="name">{d.kind === 'description' ? 'Claim description' : d.kind === 'metadata' ? 'Submission metadata' : d.filename}</div>
            <div className="meta">
              <span className="pill">{d.doc_type.replace(/_/g, ' ')}</span>
              {d.language && d.language !== 'en' && <span className="pill">{d.language}</span>}
              <TrustBadge trust={d.trust} reasons={d.trust_reasons} />
              {d.issuer && <span className="faint small">{d.issuer}</span>}
            </div>
          </span>
        </button>
      ))}
    </div>
  )
}

export function DocumentViewer() {
  const { documents, selectedDoc, highlight, docUrl } = useEvidence()
  const doc = documents.find((d) => d.id === selectedDoc) ?? documents[0]
  const firstHl = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    firstHl.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [highlight])
  if (!doc) return null
  const lit = new Set(highlight?.docId === doc.id ? highlight.lines : [])
  const isFile = doc.kind !== 'description' && doc.kind !== 'metadata'
  return (
    <section className="panel">
      <header className="panel-head">
        <h2 className="grow" style={{ wordBreak: 'break-word' }}>{isFile ? doc.filename : doc.kind === 'description' ? 'Claim description' : 'Submission metadata'}</h2>
        <span className="pill">{doc.doc_type.replace(/_/g, ' ')}</span>
        <TrustBadge trust={doc.trust} reasons={doc.trust_reasons} />
        {isFile && <a className="btn sm" href={docUrl(doc.id)} target="_blank" rel="noreferrer">open</a>}
      </header>
      <div className="panel-body">
        {doc.issuer && <div className="muted small" style={{ marginBottom: 8 }}>Issuer: {doc.issuer}{doc.language !== 'en' ? ` · language: ${doc.language}` : ''}</div>}
        {doc.summary && <div className="muted small" style={{ marginBottom: 8 }}>{doc.summary}</div>}
        {doc.kind === 'image' && <img className="viewer-img" src={docUrl(doc.id)} alt={doc.filename} />}
        {doc.kind === 'image' && doc.lines.length > 0 && <h3 style={{ margin: '4px 0 8px' }}>Transcript</h3>}
        {doc.lines.length === 0 && <div className="muted">No readable content.</div>}
        <div className="lines">
          {doc.lines.map((l, i) => {
            const n = i + 1
            const hl = lit.has(n)
            return (
              <div key={n} ref={hl && !firstHl.current ? firstHl : hl && n === Math.min(...lit) ? firstHl : null} className={`line ${hl ? 'hl' : ''}`}>
                <span className="n">{n}</span><span>{l}</span>
              </div>
            )
          })}
        </div>
        {doc.alteration_signs.length > 0 && (
          <div className="alter">
            <strong>Alteration signs noticed</strong>
            <ul>{doc.alteration_signs.map((s, i) => <li key={i}>{s}</li>)}</ul>
          </div>
        )}
        {doc.trust_reasons.length > 0 && doc.alteration_signs.length === 0 && (
          <div className="trustlist muted small" style={{ marginTop: 10 }}>
            <strong>Trust notes</strong>
            <ul>{doc.trust_reasons.map((s, i) => <li key={i}>{s}</li>)}</ul>
          </div>
        )}
      </div>
    </section>
  )
}
