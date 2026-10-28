import { useEffect, useState } from 'react'
import { NavLink, useParams } from 'react-router-dom'
import { api } from '../api'
import type { PolicyDescription } from '../types'
import { Markdown } from '../components/Markdown'

export default function PolicyPage() {
  const { name } = useParams()
  const [policies, setPolicies] = useState<PolicyDescription[]>([])
  const [policy, setPolicy] = useState<PolicyDescription | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.policies().then(setPolicies).catch((e) => setError(String(e))) }, [])
  useEffect(() => {
    const target = name ?? policies[0]?.name
    if (target) api.policy(target).then(setPolicy).catch((e) => setError(String(e)))
  }, [name, policies])

  return (
    <>
      <div className="page-head">
        <div><h1>Policies</h1><div className="sub">Each policy is one short file composed from the same components. The composition on the left is read from that file; the wording on the right is the policy document it was written against.</div></div>
      </div>
      <div className="filters" style={{ marginBottom: 14 }}>
        {policies.map((p) => <NavLink key={p.name} to={`/policies/${p.name}`} className={({ isActive }) => (isActive || (!name && p.name === policies[0]?.name)) ? 'on btn sm' : 'btn sm'}>{p.title}</NavLink>)}
      </div>
      {error && <div className="error">{error}</div>}
      {policy && (
        <div className="policy-grid">
          <div className="stack">
            <section className="panel">
              <header className="panel-head"><h2>Coverages</h2><span className="muted small">match question · required proof · checks · payout formula</span></header>
              <div className="panel-body">
                {policy.coverages.map((c) => (
                  <div className="cov-card" key={c.name}>
                    <div className="row"><strong>{c.title}</strong><span className="pill">{c.name}</span></div>
                    <dl>
                      <dt>matches</dt><dd>{c.matches.description} <span className="pill">{c.matches.id}</span></dd>
                      <dt>proofs</dt><dd className="mono">{c.proofs}</dd>
                      {c.checks.length > 0 && <><dt>checks</dt><dd>{c.checks.map((k) => <div key={k.id}>{k.description} <span className="pill">{k.id}</span></div>)}</dd></>}
                      <dt>payout</dt><dd className="mono">{c.payout ?? '–'}</dd>
                    </dl>
                  </div>
                ))}
              </div>
            </section>
            <section className="panel">
              <header className="panel-head"><h2>Exclusions</h2><span className="muted small">any one failing denies the claim</span></header>
              <div className="panel-body tight">
                <table><tbody>{policy.exclusions.map((e) => <tr key={e.id}><td>{e.description}</td><td className="num"><span className="pill">{e.id}</span></td></tr>)}</tbody></table>
              </div>
            </section>
            <section className="panel">
              <header className="panel-head"><h2>Facts the extractor looks for</h2></header>
              <div className="panel-body tight">
                <table><tbody>{policy.facts.map((f) => <tr key={f.name}><td><strong>{f.name}</strong> <span className="pill">{f.type}</span></td><td className="muted">{f.hint}</td></tr>)}</tbody></table>
              </div>
            </section>
            <section className="panel">
              <header className="panel-head"><h2>Document types</h2></header>
              <div className="panel-body tight">
                <table><tbody>{Object.entries(policy.doc_types).map(([k, v]) => <tr key={k}><td><span className="pill">{k}</span></td><td className="muted">{v}</td></tr>)}</tbody></table>
              </div>
            </section>
          </div>
          <section className="panel">
            <header className="panel-head"><h2>Policy wording</h2></header>
            <div className="panel-body"><Markdown text={policy.text ?? ''} /></div>
          </section>
        </div>
      )}
    </>
  )
}
