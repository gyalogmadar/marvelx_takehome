import { useEffect, useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import type { Health, PolicyDescription } from '../types'

export default function SubmitClaim({ health }: { health: Health | null }) {
  const [policies, setPolicies] = useState<PolicyDescription[]>([])
  const [policy, setPolicy] = useState('')
  const [description, setDescription] = useState('')
  const [submittedAt, setSubmittedAt] = useState('')
  const [meta, setMeta] = useState<{ k: string; v: string }[]>([{ k: '', v: '' }])
  const [files, setFiles] = useState<File[]>([])
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.policies().then((p) => { setPolicies(p); if (p[0] && !policy) setPolicy(p[0].name) }).catch((e) => setError(String(e)))
  }, [])

  const addFiles = (list: FileList | null) => {
    if (!list) return
    setFiles((f) => [...f, ...Array.from(list).filter((n) => !f.some((x) => x.name === n.name))])
  }
  const onDrop = (e: DragEvent) => { e.preventDefault(); setOver(false); addFiles(e.dataTransfer.files) }

  const submit = async () => {
    setBusy(true); setError(null)
    try {
      const form = new FormData()
      form.set('description', description)
      form.set('policy', policy)
      if (submittedAt) form.set('submitted_at', submittedAt)
      form.set('metadata', JSON.stringify(Object.fromEntries(meta.filter((m) => m.k.trim()).map((m) => [m.k.trim(), m.v]))))
      files.forEach((f) => form.append('files', f, f.name))
      const r = await api.submitClaim(form)
      navigate(`/claims/${r.claim_id}`)
    } catch (e) {
      setError(String(e)); setBusy(false)
    }
  }

  const selected = policies.find((p) => p.name === policy)
  return (
    <>
      <div className="page-head"><div><h1>Submit a claim</h1><div className="sub">The claim is processed in the background; you will land on its evidence page.</div></div></div>
      {health?.backend === 'scripted' && (
        <div className="notice" style={{ marginBottom: 14 }}>
          The API runs with the <strong>scripted</strong> backend: no model is called. Attach an <code>oracle.json</code> (as in <code>mockclaims/</code>) to script the model's answers; without one every judgment is unknown and the claim is referred.
        </div>
      )}
      <div className="form">
        <div className="field">
          <label>Policy</label>
          <select value={policy} onChange={(e) => setPolicy(e.target.value)}>
            {policies.map((p) => <option key={p.name} value={p.name}>{p.title} ({p.name})</option>)}
          </select>
          {selected && <div className="small muted">Proof types this policy recognises: {Object.keys(selected.doc_types).filter((t) => !['other', 'unreadable'].includes(t)).map((t) => t.replace(/_/g, ' ')).join(', ')}</div>}
        </div>
        <div className="field">
          <label>Description of what happened</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Who you are, what happened, when, and what you are claiming for." />
        </div>
        <div className="field">
          <label>Submission date (defaults to today)</label>
          <input type="date" value={submittedAt} onChange={(e) => setSubmittedAt(e.target.value)} style={{ maxWidth: 220 }} />
        </div>
        <div className="field">
          <label>Metadata</label>
          {meta.map((m, i) => (
            <div className="kv" key={i}>
              <input type="text" placeholder="key, e.g. claimant_name" value={m.k} onChange={(e) => setMeta(meta.map((x, j) => j === i ? { ...x, k: e.target.value } : x))} />
              <input type="text" placeholder="value" value={m.v} onChange={(e) => setMeta(meta.map((x, j) => j === i ? { ...x, v: e.target.value } : x))} />
              <button type="button" className="btn sm" onClick={() => setMeta(meta.length > 1 ? meta.filter((_, j) => j !== i) : [{ k: '', v: '' }])}>remove</button>
            </div>
          ))}
          <div><button type="button" className="btn sm" onClick={() => setMeta([...meta, { k: '', v: '' }])}>add field</button></div>
        </div>
        <div className="field">
          <label>Supporting documents</label>
          <div className={`dropzone ${over ? 'over' : ''}`} onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
            Drop images, markdown or text files here, or <label style={{ color: 'var(--accent)', cursor: 'pointer' }}>browse<input type="file" multiple hidden onChange={(e) => addFiles(e.target.files)} /></label>
            {files.length > 0 && (
              <div className="filelist">
                {files.map((f) => <span key={f.name} className="chip" onClick={() => setFiles(files.filter((x) => x !== f))} title="remove">{f.name} <span className="muted">✕</span></span>)}
              </div>
            )}
          </div>
        </div>
        {error && <div className="error">{error}</div>}
        <div className="row">
          <button type="button" className="btn primary" disabled={busy || !description.trim() || !policy} onClick={submit}>
            {busy && <span className="spinner" />} Submit claim
          </button>
        </div>
      </div>
    </>
  )
}
