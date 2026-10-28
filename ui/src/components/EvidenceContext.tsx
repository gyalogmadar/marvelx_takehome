import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import type { ClaimSource } from '../api'
import { documentUrl } from '../api'
import type { Document, Fact, Observation } from '../types'

/** Shared state for the evidence chain: which document is open and which lines are lit. */
export interface Highlight { docId: string; lines: number[] }

interface EvidenceState {
  documents: Document[]
  facts: Record<string, Fact>
  selectedDoc: string | null
  highlight: Highlight | null
  selectDoc: (id: string | null) => void
  showObservation: (obs: Observation) => void
  showQuote: (quote: string, docIds: string[]) => void
  showFact: (name: string) => void
  clearHighlight: () => void
  docUrl: (docId: string) => string
  docById: (id: string) => Document | undefined
}

const Ctx = createContext<EvidenceState | null>(null)

export function EvidenceProvider({ source, documents, facts, children }:
  { source: ClaimSource; documents: Document[]; facts: Fact[]; children: ReactNode }) {
  const factMap = useMemo(() => Object.fromEntries(facts.map((f) => [f.name, f])), [facts])
  const firstFile = documents.find((d) => d.kind !== 'description' && d.kind !== 'metadata') ?? documents[0]
  const [selectedDoc, setSelectedDoc] = useState<string | null>(firstFile?.id ?? null)
  const [highlight, setHighlight] = useState<Highlight | null>(null)

  const docById = useCallback((id: string) => documents.find((d) => d.id === id), [documents])

  const showObservation = useCallback((obs: Observation) => {
    setSelectedDoc(obs.document_id)
    const doc = documents.find((d) => d.id === obs.document_id)
    let lines: number[] = obs.line ? [obs.line] : []
    if (!lines.length && doc && obs.quote) {
      lines = doc.lines.map((l, i) => (l.includes(obs.quote) ? i + 1 : 0)).filter(Boolean)
    }
    setHighlight({ docId: obs.document_id, lines })
  }, [documents])

  const showQuote = useCallback((quote: string, docIds: string[]) => {
    const candidates = docIds.length ? docIds.map(docById).filter(Boolean) as Document[] : documents
    for (const doc of candidates) {
      const lines = doc.lines.map((l, i) => (l.includes(quote) || quote.includes(l) ? i + 1 : 0)).filter(Boolean)
      if (lines.length) {
        setSelectedDoc(doc.id)
        setHighlight({ docId: doc.id, lines })
        return
      }
    }
    if (docIds[0]) setSelectedDoc(docIds[0])
  }, [documents, docById])

  const showFact = useCallback((name: string) => {
    const fact = factMap[name]
    if (!fact?.observations.length) return
    const best = [...fact.observations].sort((a, b) => b.confidence - a.confidence)[0]
    showObservation(best)
  }, [factMap, showObservation])

  const value = useMemo<EvidenceState>(() => ({
    documents, facts: factMap, selectedDoc, highlight,
    selectDoc: setSelectedDoc, showObservation, showQuote, showFact,
    clearHighlight: () => setHighlight(null),
    docUrl: (id) => documentUrl(source, id), docById,
  }), [documents, factMap, selectedDoc, highlight, showObservation, showQuote, showFact, source, docById])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useEvidence(): EvidenceState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useEvidence outside EvidenceProvider')
  return v
}
