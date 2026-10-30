import type { ModelCall } from '../types'

export function CallsTable({ calls, logHref }: { calls: ModelCall[]; logHref?: string }) {
  if (!calls.length) return null
  const byComponent = new Map<string, { calls: number; input: number; cache: number; output: number; cost: number; ms: number; errors: number; models: Set<string> }>()
  for (const c of calls) {
    const row = byComponent.get(c.component) ?? { calls: 0, input: 0, cache: 0, output: 0, cost: 0, ms: 0, errors: 0, models: new Set<string>() }
    row.calls++; row.input += c.input_tokens; row.cache += c.cache_read_tokens; row.output += c.output_tokens
    row.cost += c.cost_usd; row.ms += c.latency_ms; row.errors += c.ok ? 0 : 1; row.models.add(c.model)
    byComponent.set(c.component, row)
  }
  const total = [...byComponent.values()].reduce((s, r) => s + r.cost, 0)
  return (
    <section className="panel">
      <header className="panel-head"><h2>Model calls</h2>
        {logHref && <a className="btn sm" href={logHref} target="_blank" rel="noreferrer" title="every request and response in full, with reasoning">full log</a>}
        <span className="muted small right">total ${total.toFixed(4)}</span></header>
      <div className="panel-body tight">
        <table>
          <thead><tr><th>component</th><th>model</th><th className="num">calls</th><th className="num">input</th><th className="num">cached</th><th className="num">output</th><th className="num">cost</th><th className="num">ms</th></tr></thead>
          <tbody>
            {[...byComponent.entries()].map(([name, r]) => (
              <tr key={name}>
                <td>{name.replace(/_/g, ' ')}{r.errors ? <span className="badge failed" style={{ marginLeft: 6 }}>{r.errors} failed</span> : null}</td>
                <td className="mono muted">{[...r.models].join(', ')}</td>
                <td className="num">{r.calls}</td><td className="num">{r.input}</td><td className="num">{r.cache}</td><td className="num">{r.output}</td>
                <td className="num">${r.cost.toFixed(4)}</td><td className="num">{r.ms}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
