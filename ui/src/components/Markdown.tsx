import type { ReactNode } from 'react'

/** Enough markdown for policy documents: headings, bullets, rules, bold, inline code. */
export function Markdown({ text }: { text: string }) {
  const out: ReactNode[] = []
  let list: ReactNode[] = []
  const flush = () => { if (list.length) { out.push(<ul key={`ul${out.length}`}>{list}</ul>); list = [] } }
  text.split('\n').forEach((raw, i) => {
    const line = raw.trimEnd()
    if (!line.trim()) { flush(); return }
    if (/^---+$/.test(line)) { flush(); out.push(<hr key={i} />); return }
    const h = /^(#{1,3})\s+(.*)$/.exec(line)
    if (h) { flush(); const Tag = (`h${h[1].length}`) as 'h1' | 'h2' | 'h3'; out.push(<Tag key={i}>{inline(h[2])}</Tag>); return }
    const li = /^[-*]\s+(.*)$/.exec(line)
    if (li) { list.push(<li key={i}>{inline(li[1])}</li>); return }
    flush(); out.push(<p key={i}>{inline(line)}</p>)
  })
  flush()
  return <div className="md">{out}</div>
}

function inline(s: string): ReactNode[] {
  const parts: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0, m: RegExpExecArray | null, k = 0
  while ((m = re.exec(s))) {
    if (m.index > last) parts.push(s.slice(last, m.index))
    const t = m[0]
    parts.push(t.startsWith('`') ? <code key={k++}>{t.slice(1, -1)}</code> : <strong key={k++}>{t.slice(2, -2)}</strong>)
    last = m.index + t.length
  }
  if (last < s.length) parts.push(s.slice(last))
  return parts
}
