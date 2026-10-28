import type { CheckResult } from '../types'
import { Confidence, KindTag, VerdictIcon } from './Badges'
import { FactChip, DocChip } from './FactChip'
import { useEvidence } from './EvidenceContext'

export function CheckRow({ check, determining, collapsed }: { check: CheckResult; determining?: boolean; collapsed?: boolean }) {
  const { showQuote } = useEvidence()
  const rationale = check.rationale.replace(/^answer: (yes|no|unknown)\.\s*/i, '')
  const answer = /^answer: (yes|no|unknown)/i.exec(check.rationale)?.[1]
  return (
    <div id={`check-${check.id}`} className={`check ${determining ? 'determining' : ''} ${collapsed ? 'collapsed' : ''}`}>
      <VerdictIcon verdict={check.verdict} />
      <div>
        <div className="row wrap">
          <span className="title">{check.description}</span>
          <KindTag kind={check.kind} />
          {answer && <span className="pill">answer: {answer}</span>}
          <span className="right"><Confidence value={check.confidence} /></span>
        </div>
        {!collapsed && (
          <>
            <div className="rationale">{rationale}</div>
            {check.quote && (
              <blockquote className="quote" title="show in document" onClick={() => showQuote(check.quote!, check.document_ids)}>
                “{check.quote}”
              </blockquote>
            )}
            {(check.facts_used.length > 0 || check.document_ids.length > 0) && (
              <div className="facts">
                {check.facts_used.map((f) => <FactChip key={f} name={f} />)}
                {check.facts_used.length === 0 && check.document_ids.map((d) => <DocChip key={d} id={d} />)}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
