import { useState } from 'react'
import { PATH_COLORS, SOURCE_LABELS, groupEvidence, rttBand } from '../../lib/overlays.js'
import AsciiJackal from '../ui/AsciiJackal.jsx'
import { exportUrl } from '../../lib/api.js'
import BulkPanel from './BulkPanel.jsx'
import HistoryPanel from './HistoryPanel.jsx'

const EXPORTS = [
  ['json', 'JSON (full report)'],
  ['geojson', 'GeoJSON'],
  ['kml', 'KML (Google Earth)'],
  ['md', 'Markdown report'],
  ['csv', 'CSV of hops'],
]

function Actions({ run, onRerun, onSnapshot }) {
  return (
    <div className="flex flex-wrap gap-2 font-mono text-[11.5px]">
      <details className="relative">
        <summary className="cursor-pointer list-none border border-accent bg-accent px-2.5 py-1 font-bold text-accent-ink">
          Export ▾
        </summary>
        <ul className="absolute left-0 z-20 mt-1 grid w-[200px] border border-line bg-sunken py-1 shadow-lg">
          {EXPORTS.map(([format, label]) => (
            <li key={format}>
              <a href={exportUrl(run.id, format)} download className="block px-2.5 py-1 hover:bg-raised">
                {label}
              </a>
            </li>
          ))}
          <li>
            <button type="button" onClick={onSnapshot} className="block w-full px-2.5 py-1 text-left hover:bg-raised">
              Map as PNG
            </button>
          </li>
        </ul>
      </details>
      <button
        type="button"
        onClick={() => onRerun(run.query)}
        className="border border-line bg-raised px-2.5 py-1 hover:border-accent"
      >
        Re-run
      </button>
    </div>
  )
}

const TABS = [
  { id: 'summary', label: 'Summary' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'hops', label: 'Hops' },
  { id: 'probes', label: 'Probes' },
  { id: 'raw', label: 'Raw' },
  { id: 'history', label: 'History' },
]

const LIMITS = [
  "Residential: the best answer is the ISP's PoP or BNG city.",
  'Mobile and CGNAT: the gateway can be hundreds of km from the user.',
  'VPN, Tor, proxies, hosting: you locate the exit server.',
  'Anycast: there is no single location.',
]

const KIND_CLASS = {
  hard: 'border-accent text-accent-fg',
  soft: 'border-accent2 text-accent2',
  negative: 'border-high text-high',
  info: 'border-line text-fg-subtle',
}
const STATUS_CLASS = { ok: 'text-good', empty: 'text-fg-subtle', skipped: 'text-warn', error: 'text-high' }

function Heading({ children }) {
  return <h2 className="font-mono text-[10.5px] font-medium uppercase tracking-widest text-fg-subtle">{children}</h2>
}

function Tag({ kind }) {
  return (
    <span
      className={`self-start whitespace-nowrap border px-1.5 font-mono text-[10px] uppercase tracking-wide ${KIND_CLASS[kind]}`}
    >
      {kind}
    </span>
  )
}

function Idle() {
  return (
    <>
      <div className="grid min-w-0 justify-items-center gap-3 pt-2 text-center">
        <AsciiJackal mood="closed" tone="muted" preset="medium" />
        <div className="grid min-w-0 gap-1">
          <b className="font-mono text-sm font-medium">Nothing to locate yet</b>
          <span className="text-fg-muted">Type an IP address or a hostname in the bar above and press Run.</span>
        </div>
      </div>
      <section className="grid gap-2">
        <Heading>What a run does</Heading>
        <ul className="grid list-disc gap-1 pl-4 text-fg-muted">
          <li>Reads passive sources: GeoIP databases, RIPE IPmap, RIPEstat, RDAP, reverse DNS, PeeringDB.</li>
          <li>Classifies the network (residential, mobile, hosting, CDN) to say what the answer can mean.</li>
          <li>Pings the target from about 50 probes worldwide, then about 30 near the fastest ones.</li>
          <li>Intersects the speed-of-light disks into a feasible region and detects anycast.</li>
          <li>Traces the paths from the fastest probes and reads router names for their cities.</li>
          <li>Weighs everything into one estimate with a confidence and the reasons behind it.</li>
        </ul>
      </section>
      <section className="grid gap-2">
        <Heading>Known limits</Heading>
        <ul className="grid list-disc gap-1 pl-4 text-fg-muted">
          {LIMITS.map((limit) => (
            <li key={limit}>{limit}</li>
          ))}
        </ul>
      </section>
    </>
  )
}

function Running({ run }) {
  return (
    <div className="grid min-w-0 justify-items-center gap-3 pt-2 text-center">
      <AsciiJackal mood="animated" preset="medium" />
      <div className="grid gap-1">
        <b className="font-mono text-sm font-medium">Looking up {run.query}</b>
        <span className="text-fg-muted">
          Reading the passive sources, then measuring. Findings appear as they arrive.
        </span>
      </div>
    </div>
  )
}

function Failed({ run }) {
  return (
    <div className="grid min-w-0 justify-items-center gap-3 pt-2 text-center">
      <AsciiJackal mood="x" tone="muted" preset="medium" />
      <div className="grid gap-1">
        <b className="font-mono text-sm font-medium text-high">Cannot locate {run.query}</b>
        <span className="text-fg-muted">{run.error}</span>
      </div>
    </div>
  )
}

function KeyValues({ rows }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3.5 gap-y-1">
      {rows
        .filter(([, value]) => value !== null && value !== undefined && value !== '')
        .map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="font-mono text-[11.5px] text-fg-subtle">{key}</dt>
            <dd className="m-0 break-words font-mono text-[12px] tabular-nums">{value}</dd>
          </div>
        ))}
    </dl>
  )
}

function EstimateCard({ report }) {
  const estimate = report.estimate
  const region = report.region
  const where = [estimate.city, estimate.country].filter(Boolean).join(', ')
  const probes = report.probes.filter((p) => p.min_rtt !== null).length
  return (
    <section className="grid gap-2.5 border border-accent/60 bg-canvas p-3">
      <div className="font-mono text-[10.5px] uppercase tracking-widest text-fg-subtle">
        {estimate.anycast ? 'Nearest anycast site' : 'Estimate'}
      </div>
      <div className="font-mono text-[22px] font-bold leading-tight text-accent-fg">
        {where || `${estimate.location.lat.toFixed(3)}, ${estimate.location.lon.toFixed(3)}`}
      </div>
      <KeyValues
        rows={[
          ['coordinates', `${estimate.location.lat.toFixed(4)}, ${estimate.location.lon.toFixed(4)}`],
          ['uncertainty', `±${Math.round(estimate.uncertainty_km).toLocaleString('en')} km`],
          [
            'region',
            region ? `${Math.round(region.area_km2).toLocaleString('en')} km² · H3 res ${region.resolution}` : null,
          ],
          ['probes', report.probes.length ? `${probes} answered of ${report.probes.length}` : null],
        ]}
      />
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2" title="Confidence, 0 to 100">
        <div
          className="relative h-[7px] bg-line-soft"
          role="meter"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-valuenow={estimate.confidence}
          aria-label="Confidence"
        >
          <span className="absolute inset-y-0 left-0 bg-accent" style={{ width: `${estimate.confidence}%` }} />
        </div>
        <b className="font-mono text-[13px] text-accent-fg">{estimate.confidence}</b>
      </div>
      {estimate.explanation.map((line) => (
        <p key={line} className="text-[12px] text-fg-muted">
          {line}
        </p>
      ))}
    </section>
  )
}

function Candidates({ region }) {
  if (!region?.candidates?.length) return null
  return (
    <section className="grid gap-2">
      <Heading>Cities in the region</Heading>
      <table className="w-full border-collapse font-mono text-[11.5px] tabular-nums">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-fg-subtle">
            <th className="border-b border-line py-1 font-medium">City</th>
            <th className="border-b border-line py-1 text-right font-medium">From centre</th>
            <th className="border-b border-line py-1 text-right font-medium">Score</th>
          </tr>
        </thead>
        <tbody>
          {region.candidates.slice(0, 8).map((c) => (
            <tr key={`${c.name}-${c.country}`}>
              <td className="border-b border-line-soft py-1">
                {c.name} <span className="text-fg-subtle">{c.country}</span>
              </td>
              <td className="border-b border-line-soft py-1 text-right">{Math.round(c.distance_km)} km</td>
              <td className="border-b border-line-soft py-1 text-right">{c.score.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function Anycast({ anycast }) {
  if (!anycast?.detected) return null
  return (
    <section
      role="note"
      className="grid gap-1.5 border border-warn bg-[color-mix(in_srgb,var(--color-warn)_12%,transparent)] p-2.5 text-[12.5px]"
    >
      <b className="font-mono text-[12px] text-warn">Anycast</b>
      <span>{anycast.summary}.</span>
    </section>
  )
}

function Probes({ report }) {
  if (!report?.probes?.length) {
    return <p className="text-fg-muted">No measurements yet. They start after the passive sources.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse font-mono text-[11.5px] tabular-nums">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-fg-subtle">
            <th className="border-b border-line px-1 py-1 font-medium">Probe</th>
            <th className="border-b border-line px-1 py-1 font-medium">Phase</th>
            <th className="border-b border-line px-1 py-1 text-right font-medium">min RTT</th>
            <th className="border-b border-line px-1 py-1 text-right font-medium">d max</th>
          </tr>
        </thead>
        <tbody>
          {report.probes.map((p) => (
            <tr key={`${p.probe.provider}-${p.probe.probe_id}`}>
              <td className="border-b border-line-soft px-1 py-1">
                <span
                  aria-hidden="true"
                  className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle"
                  style={{
                    background: p.min_rtt === null ? 'var(--color-fg-subtle)' : `var(--color-rtt${rttBand(p.min_rtt)})`,
                  }}
                />
                {[p.probe.city, p.probe.country].filter(Boolean).join(', ') || p.probe.probe_id}
                <span className="block pl-3.5 text-[10.5px] text-fg-subtle">
                  {[p.probe.asn ? `AS${p.probe.asn}` : null, p.probe.network, p.probe.provider]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </td>
              <td className="border-b border-line-soft px-1 py-1 text-fg-muted">{p.phase}</td>
              <td className="border-b border-line-soft px-1 py-1 text-right">
                {p.min_rtt === null ? <span className="text-fg-subtle">no reply</span> : `${p.min_rtt.toFixed(1)} ms`}
              </td>
              <td className="border-b border-line-soft px-1 py-1 text-right">
                {p.max_distance_km === null ? '—' : `${Math.round(p.max_distance_km).toLocaleString('en')} km`}
                {p.calibrated ? <span className="block text-[10px] text-accent2">calibrated</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const FLAG_TEXT = {
  mpls_or_hidden_hops: 'MPLS or hidden hops',
  rtt_decreases: 'RTT falls along the path (asymmetric return)',
  unresponsive_tail: 'the last hops do not answer',
  target_not_reached: 'the target did not answer',
}

function Hops({ report }) {
  const [selected, setSelected] = useState(0)
  const traces = report?.traceroutes ?? []
  if (!traces.length) {
    return <p className="text-fg-muted">No traceroutes yet. They run from the fastest probes after the pings.</p>
  }
  const trace = traces[Math.min(selected, traces.length - 1)]
  const target = report.target.address
  return (
    <>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Path">
        {traces.map((t, i) => (
          <button
            key={`${t.probe.provider}-${t.probe.probe_id}`}
            type="button"
            aria-pressed={i === selected}
            onClick={() => setSelected(i)}
            className={[
              'border px-2 py-0.5 font-mono text-[11px]',
              i === selected ? 'border-current' : 'border-line opacity-70',
            ].join(' ')}
            style={{ color: `var(--color-${PATH_COLORS[i % PATH_COLORS.length]})` }}
          >
            {t.probe.city || t.probe.probe_id}
          </button>
        ))}
      </div>
      <p className="text-[12px] text-fg-muted">
        {trace.protocol} from {[trace.probe.city, trace.probe.country].filter(Boolean).join(', ')}
        {trace.reached ? ' · reached the target' : ''}
        {trace.flags.length ? ` · ${trace.flags.map((f) => FLAG_TEXT[f] ?? f).join(', ')}` : ''}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-mono text-[11.5px] tabular-nums">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-fg-subtle">
              <th className="border-b border-line px-1 py-1 font-medium">#</th>
              <th className="border-b border-line px-1 py-1 font-medium">Hop</th>
              <th className="border-b border-line px-1 py-1 text-right font-medium">RTT</th>
              <th className="border-b border-line px-1 py-1 font-medium">Where</th>
            </tr>
          </thead>
          <tbody>
            {trace.hops.map((hop) => {
              const converge = report.convergence?.address && hop.address === report.convergence.address
              const isTarget = hop.address === target
              return (
                <tr key={hop.ttl} className={converge ? 'bg-accent2-soft' : isTarget ? 'bg-accent-soft' : ''}>
                  <td className="border-b border-line-soft px-1 py-1 text-fg-subtle">{hop.ttl}</td>
                  <td className="max-w-[170px] break-all border-b border-line-soft px-1 py-1">
                    {hop.address ? (hop.hostname ?? hop.address) : <span className="text-fg-subtle">* no reply</span>}
                    <span className="block text-[10.5px] text-fg-subtle">
                      {[hop.hostname ? hop.address : null, hop.asn ? `AS${hop.asn}` : null, hop.ixp]
                        .filter(Boolean)
                        .join(' · ')}
                      {isTarget ? ' target' : ''}
                      {converge ? ' convergence' : ''}
                    </span>
                  </td>
                  <td className="border-b border-line-soft px-1 py-1 text-right">
                    {hop.min_rtt === null ? '—' : `${hop.min_rtt.toFixed(1)} ms`}
                  </td>
                  <td className="border-b border-line-soft px-1 py-1">
                    {hop.location_label ? (
                      <>
                        <span className="text-accent-fg">{hop.location_label}</span>
                        <span className="block text-[10.5px] text-fg-subtle">
                          {hop.location_source === 'ixp' ? 'IXP' : `name: ${hop.hints[0]?.token ?? ''}`}
                        </span>
                      </>
                    ) : hop.hints.length ? (
                      <span className="text-fg-subtle" title="The RTT rules this hint out">
                        {hop.hints[0].name}? (too far)
                      </span>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

function PathSummary({ report }) {
  const conv = report.convergence
  const access = report.access
  if (!conv && !access) return null
  return (
    <section className="grid gap-2">
      <Heading>Paths</Heading>
      <KeyValues
        rows={[
          [
            'convergence',
            conv
              ? `${conv.paths}/${conv.of} paths at ${conv.hostname ?? conv.address}${conv.label ? ` (${conv.label})` : ''}`
              : null,
          ],
          ['to target', conv?.rtt_to_target_ms != null ? `${conv.rtt_to_target_ms.toFixed(1)} ms after it` : null],
          ['access', access ? `${access.delay_ms.toFixed(1)} ms · ${access.technology}` : null],
        ]}
      />
    </section>
  )
}

function Summary({ report, running, run, onRerun, onSnapshot }) {
  const net = report.network ?? {}
  const located = report.evidence.filter((e) => e.point && e.source !== 'peeringdb')
  return (
    <>
      {report.caveats.map((caveat) => (
        <div
          key={caveat}
          role="note"
          className="grid grid-cols-[auto_1fr] gap-2.5 border border-info bg-info-soft px-2.5 py-2 text-[12.5px]"
        >
          <span
            aria-hidden="true"
            className="grid h-[17px] w-[17px] place-items-center border border-info font-mono text-[11px] font-bold text-info"
          >
            i
          </span>
          <span>{caveat}</span>
        </div>
      ))}
      <Anycast anycast={report.anycast} />
      {report.estimate ? <EstimateCard report={report} /> : null}
      {!running ? <Actions run={run} onRerun={onRerun} onSnapshot={onSnapshot} /> : null}
      <Candidates region={report.region} />
      <PathSummary report={report} />
      <section className="grid gap-2.5 border border-line bg-canvas p-3">
        <div>
          <div className="min-w-0">
            <div className="font-mono text-[10.5px] uppercase tracking-widest text-fg-subtle">Target</div>
            <div className="break-all font-mono text-[20px] font-bold leading-tight text-accent-fg">
              {report.target.address}
            </div>
            {report.target.hostname ? (
              <div className="font-mono text-[12px] text-fg-muted">{report.target.hostname}</div>
            ) : null}
          </div>
        </div>
        <KeyValues
          rows={[
            ['prefix', net.prefix],
            ['origin', net.asn ? `AS${net.asn} ${net.as_name ?? ''}` : null],
            ['registry', [net.rir, net.rir_country].filter(Boolean).join(' · ')],
            ['netname', net.netname],
            ['holder', net.holder],
            ['network', report.classification.join(' + ') || (running ? '…' : 'unclassified')],
          ]}
        />
      </section>
      <section className="grid gap-2">
        <Heading>Where the sources put it</Heading>
        {located.length ? (
          <ul className="grid gap-1.5">
            {located.map((e, i) => (
              <li key={i} className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 text-[12.5px]">
                <span className="min-w-0">
                  <b className="font-medium">{e.label ?? e.source}</b>{' '}
                  <span className="text-fg-muted">{[e.city, e.country].filter(Boolean).join(', ') || '—'}</span>
                </span>
                <span className="font-mono text-[11px] text-fg-subtle">
                  {e.radius_km ? `±${Math.round(e.radius_km)} km` : ''}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-fg-muted">
            {running ? 'Waiting for the sources…' : 'No source placed this address on the map.'}
          </p>
        )}
        <p className="text-[12px] text-fg-subtle">
          These are passive answers. The RTT region above confirms or rules them out.
        </p>
      </section>
      <section className="grid gap-2">
        <Heading>Sources</Heading>
        <ul className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11.5px]">
          {report.providers.map((p) => (
            <li key={p.provider} title={p.message}>
              <span className={STATUS_CLASS[p.status]}>{SOURCE_LABELS[p.provider] ?? p.provider}</span>
              <span className="text-fg-subtle"> {p.status === 'ok' ? '' : p.status}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  )
}

function detailRows(item) {
  const d = item.details ?? {}
  const rows = []
  for (const [key, value] of Object.entries(d)) {
    if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) continue
    if (typeof value === 'object' && !Array.isArray(value)) continue
    const shown = Array.isArray(value)
      ? value.map((v) => (typeof v === 'object' ? Object.values(v).filter(Boolean).join(' ') : v)).join(', ')
      : String(value)
    rows.push([key.replaceAll('_', ' '), shown.length > 300 ? `${shown.slice(0, 300)}…` : shown])
  }
  return rows
}

function Evidence({ report }) {
  const groups = groupEvidence(report)
  if (!groups.length) return <p className="text-fg-muted">No evidence yet.</p>
  return groups.map(({ source, items }) => (
    <section key={source} className="grid gap-2">
      <Heading>
        {SOURCE_LABELS[source] ?? source} <span className="text-fg-subtle">· {items.length}</span>
      </Heading>
      <ul className="grid gap-2">
        {items.slice(0, 40).map((item, i) => (
          <li key={i} className="grid gap-1.5 border border-line-soft bg-canvas p-2.5">
            <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-2 text-[12.5px]">
              <Tag kind={item.kind} />
              <span className="min-w-0 break-words">{item.summary}</span>
            </div>
            {detailRows(item).length ? (
              <details className="text-[12px]">
                <summary className="cursor-pointer font-mono text-[11px] text-fg-subtle">details</summary>
                <div className="pt-1.5">
                  <KeyValues rows={detailRows(item)} />
                </div>
              </details>
            ) : null}
          </li>
        ))}
        {items.length > 40 ? (
          <li className="font-mono text-[11px] text-fg-subtle">… {items.length - 40} more in Raw</li>
        ) : null}
      </ul>
    </section>
  ))
}

export default function SidePanel({
  run,
  targetIndex,
  onTargetChange,
  onRerun,
  onSnapshot,
  onOpenRun,
  bulk,
  onCancelBulk,
  onCloseBulk,
}) {
  const [tab, setTab] = useState('summary')
  const current = TABS.find((t) => t.id === tab)
  const report = run?.targets?.[targetIndex] ?? null
  const running = run?.status === 'running'

  let body
  if (bulk) {
    return (
      <aside className="flex min-h-0 min-w-0 flex-col border-l border-line bg-panel max-[860px]:border-l-0 max-[860px]:border-t">
        <div className="border-b border-line bg-sunken px-3.5 py-2 font-mono text-[11.5px] text-accent-fg">
          Bulk run · {bulk.items.length} targets
        </div>
        <div className="flex-1 overflow-auto p-3.5">
          <BulkPanel job={bulk} onCancel={onCancelBulk} onOpen={onOpenRun} onClose={onCloseBulk} />
        </div>
      </aside>
    )
  }
  if (tab === 'history') {
    body = run ? (
      <HistoryPanel query={run.query} currentId={run.id} onOpen={onOpenRun} />
    ) : (
      <HistoryPanel query={null} currentId={null} onOpen={onOpenRun} />
    )
  } else if (current.from) {
    body = (
      <p className="border border-dashed border-line px-1 py-6 text-center font-mono text-xs text-fg-subtle">
        {current.what} will appear here from v{current.from}.
      </p>
    )
  } else if (tab === 'hops') {
    body = <Hops report={report} />
  } else if (tab === 'probes') {
    body = <Probes report={report} />
  } else if (tab === 'raw') {
    body = run ? (
      <pre className="m-0 overflow-auto border border-line bg-canvas p-2.5 font-mono text-[11px] leading-normal text-fg-muted">
        {JSON.stringify(run, null, 2)}
      </pre>
    ) : (
      <p className="text-fg-muted">Run a target to see its raw report.</p>
    )
  } else if (!run) {
    body = <Idle />
  } else if (run.status === 'failed') {
    body = <Failed run={run} />
  } else if (!report) {
    body = <Running run={run} />
  } else {
    body =
      tab === 'summary' ? (
        <Summary report={report} running={running} run={run} onRerun={onRerun} onSnapshot={onSnapshot} />
      ) : (
        <Evidence report={report} />
      )
  }

  return (
    <aside className="flex min-h-0 min-w-0 flex-col border-l border-line bg-panel max-[860px]:border-l-0 max-[860px]:border-t">
      <div
        className="scrollbar-none flex overflow-x-auto border-b border-line bg-sunken"
        role="tablist"
        aria-label="Details"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="panel-body"
            onClick={() => setTab(t.id)}
            className={[
              'whitespace-nowrap border-b-2 px-2.5 pb-[7px] pt-2 font-mono text-[11.5px]',
              tab === t.id ? 'border-accent text-accent-fg' : 'border-transparent text-fg-muted hover:text-fg',
            ].join(' ')}
          >
            {t.label}
            {t.id === 'evidence' && report ? (
              <span className="ml-1 text-fg-subtle">{report.evidence.length}</span>
            ) : null}
            {t.id === 'probes' && report?.probes?.length ? (
              <span className="ml-1 text-fg-subtle">{report.probes.length}</span>
            ) : null}
          </button>
        ))}
      </div>
      {run?.targets?.length > 1 ? (
        <div className="flex flex-wrap gap-1.5 border-b border-line-soft px-3.5 py-2" aria-label="Addresses">
          {run.targets.map((t, i) => (
            <button
              key={t.target.address}
              type="button"
              aria-pressed={i === targetIndex}
              onClick={() => onTargetChange(i)}
              className={[
                'border px-2 py-0.5 font-mono text-[11px]',
                i === targetIndex ? 'border-accent bg-accent-soft text-accent-fg' : 'border-line text-fg-muted',
              ].join(' ')}
            >
              {t.target.address}
            </button>
          ))}
        </div>
      ) : null}
      <div
        id="panel-body"
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        className="grid flex-1 content-start gap-4 overflow-auto p-3.5"
      >
        {body}
      </div>
    </aside>
  )
}
