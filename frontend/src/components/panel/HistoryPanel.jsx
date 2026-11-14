import { useEffect, useState } from 'react'
import { fetchDiff, fetchHistory } from '../../lib/api.js'

const pad = (n) => String(n).padStart(2, '0')

/** Local date and time, YYYY-MM-DD HH:MM. */
export function when(iso) {
  const d = new Date(iso)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Past runs of the current query; pick two to compare, or open one. */
export default function HistoryPanel({ query, currentId, onOpen }) {
  const [entries, setEntries] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState([])
  const [diff, setDiff] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchHistory(query, { signal: controller.signal })
      .then((rows) => {
        setEntries(rows)
        setError(null)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message)
      })
    return () => controller.abort()
  }, [query, currentId])

  useEffect(() => {
    if (selected.length !== 2) {
      setDiff(null)
      return undefined
    }
    const controller = new AbortController()
    // Oldest first, so "before" is the earlier run.
    const ordered = [...selected].sort(
      (a, b) => entries.findIndex((e) => e.id === b) - entries.findIndex((e) => e.id === a),
    )
    fetchDiff(ordered[0], ordered[1], { signal: controller.signal })
      .then(setDiff)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message)
      })
    return () => controller.abort()
  }, [selected, entries])

  const toggle = (id) =>
    setSelected((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id].slice(-2)))

  if (error) return <p className="text-high">Cannot read the history: {error}</p>
  if (!entries) return <p className="text-fg-muted">Loading…</p>
  if (!entries.length) {
    return <p className="text-fg-muted">No past runs{query ? ` of ${query}` : ''} yet. Every finished run is kept.</p>
  }
  return (
    <>
      <p className="text-[12px] text-fg-muted">
        Runs of <span className="font-mono">{query}</span>. Tick two to compare them.
      </p>
      <table className="w-full border-collapse font-mono text-[11.5px] tabular-nums">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-fg-subtle">
            <th className="border-b border-line py-1 font-medium" aria-label="Compare" />
            <th className="border-b border-line py-1 font-medium">When</th>
            <th className="border-b border-line py-1 font-medium">Estimate</th>
            <th className="border-b border-line py-1 text-right font-medium">Conf.</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.id} className={entry.id === currentId ? 'bg-accent-soft' : ''}>
              <td className="border-b border-line-soft py-1 pr-1">
                <input
                  type="checkbox"
                  id={`compare-${entry.id}`}
                  aria-label={`Compare the run of ${when(entry.started_at)}`}
                  checked={selected.includes(entry.id)}
                  onChange={() => toggle(entry.id)}
                  className="m-0 accent-[var(--color-accent)]"
                />
              </td>
              <td className="border-b border-line-soft py-1">
                <button
                  type="button"
                  className="text-left underline-offset-2 hover:underline"
                  onClick={() => onOpen(entry.id)}
                >
                  {when(entry.started_at)}
                </button>
              </td>
              <td className="border-b border-line-soft py-1">
                {[entry.city, entry.country].filter(Boolean).join(', ') || entry.status}
              </td>
              <td className="border-b border-line-soft py-1 text-right">{entry.confidence ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {diff ? (
        <section className="grid gap-2">
          <h2 className="font-mono text-[10.5px] font-medium uppercase tracking-widest text-fg-subtle">What changed</h2>
          {diff.length ? (
            <ul className="grid gap-1.5 font-mono text-[12px]">
              {diff.map((change) => (
                <li key={`${change.field}-${change.before}`} className="grid gap-0.5 border-b border-line-soft pb-1.5">
                  <span className="text-[11px] text-fg-subtle">
                    {change.field}
                    {change.note ? ` · ${change.note}` : ''}
                  </span>
                  <span>
                    <del className="text-high decoration-high/60">{change.before}</del> →{' '}
                    <ins className="text-good no-underline">{change.after}</ins>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-fg-muted">No difference between the two runs.</p>
          )}
        </section>
      ) : null}
    </>
  )
}
