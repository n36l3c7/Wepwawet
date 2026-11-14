import { bulkExportUrl } from '../../lib/api.js'

const STATUS_CLASS = {
  queued: 'text-fg-subtle',
  running: 'text-accent-fg',
  done: 'text-good',
  failed: 'text-high',
  cancelled: 'text-warn',
}

export function confidenceTone(confidence) {
  if (confidence === null || confidence === undefined) return 'var(--color-fg-subtle)'
  if (confidence >= 60) return 'var(--color-good)'
  if (confidence >= 35) return 'var(--color-warn)'
  return 'var(--color-high)'
}

/** The queue of a bulk run: progress, budget, one row per target, exports. */
export default function BulkPanel({ job, onCancel, onOpen, onClose }) {
  const done = job.items.filter((i) => i.status === 'done' || i.status === 'failed').length
  const running = job.status === 'running'
  return (
    <div className="grid content-start gap-3.5">
      <div className="grid grid-cols-4 gap-px border border-line bg-line font-mono">
        {[
          ['done', done, 'text-good'],
          ['queued', job.items.filter((i) => i.status === 'queued').length, 'text-fg'],
          ['failed', job.items.filter((i) => i.status === 'failed').length, 'text-high'],
          ['tests', job.tests_used, 'text-accent-fg'],
        ].map(([label, value, tone]) => (
          <div key={label} className="grid gap-px bg-canvas px-2 py-1.5">
            <b className={`text-[16px] tabular-nums ${tone}`}>{value}</b>
            <span className="text-[10px] uppercase tracking-wide text-fg-subtle">{label}</span>
          </div>
        ))}
      </div>
      <p className="text-[12px] text-fg-muted">
        {running ? `Locating ${done + 1} of ${job.items.length}. ` : `Finished: ${done} of ${job.items.length}. `}
        Budget {job.passive ? 'passive only' : `${job.tests_used} of ${job.budget} tests`}.
      </p>
      <div className="flex flex-wrap gap-2 font-mono text-[11.5px]">
        {running ? (
          <button
            type="button"
            onClick={onCancel}
            className="border border-line bg-raised px-2.5 py-1 hover:border-high"
          >
            Cancel
          </button>
        ) : (
          <button
            type="button"
            onClick={onClose}
            className="border border-line bg-raised px-2.5 py-1 hover:border-accent"
          >
            Close
          </button>
        )}
        <a
          href={bulkExportUrl(job.id, 'csv')}
          download
          className="border border-line bg-raised px-2.5 py-1 hover:border-accent"
        >
          Summary CSV
        </a>
        <a
          href={bulkExportUrl(job.id, 'geojson')}
          download
          className="border border-line bg-raised px-2.5 py-1 hover:border-accent"
        >
          GeoJSON
        </a>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-mono text-[11.5px] tabular-nums">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-fg-subtle">
              <th className="border-b border-line px-1 py-1 font-medium">#</th>
              <th className="border-b border-line px-1 py-1 font-medium">Target</th>
              <th className="border-b border-line px-1 py-1 font-medium">Result</th>
              <th className="border-b border-line px-1 py-1 text-right font-medium">Conf.</th>
            </tr>
          </thead>
          <tbody>
            {job.items.map((item, index) => {
              const s = item.summary ?? {}
              return (
                <tr key={item.query}>
                  <td className="border-b border-line-soft px-1 py-1 text-fg-subtle">{index + 1}</td>
                  <td className="max-w-[140px] break-all border-b border-line-soft px-1 py-1">
                    {item.run_id ? (
                      <button type="button" className="text-left hover:underline" onClick={() => onOpen(item.run_id)}>
                        {item.query}
                      </button>
                    ) : (
                      item.query
                    )}
                  </td>
                  <td className="border-b border-line-soft px-1 py-1">
                    {item.status === 'done' && s.city ? (
                      <>
                        {s.city}, {s.country}
                        <span className="block text-[10.5px] text-fg-subtle">
                          ±{Math.round(s.uncertainty_km)} km{s.anycast ? ' · anycast' : ''}
                        </span>
                      </>
                    ) : (
                      <span className={STATUS_CLASS[item.status]}>
                        {item.status}
                        {s.error ? <span className="block text-[10.5px]">{s.error}</span> : null}
                      </span>
                    )}
                  </td>
                  <td
                    className="border-b border-line-soft px-1 py-1 text-right"
                    style={{ color: confidenceTone(s.confidence) }}
                  >
                    {s.confidence ?? '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
