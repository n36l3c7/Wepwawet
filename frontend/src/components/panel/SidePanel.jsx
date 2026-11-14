import { useState } from 'react'
import { SOURCE_LABELS, groupEvidence } from '../../lib/overlays.js'
import AsciiJackal from '../ui/AsciiJackal.jsx'

const TABS = [
  { id: 'summary', label: 'Summary' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'hops', label: 'Hops', from: '0.4.0', what: 'Traceroute hops with PTR, ASN and location hints' },
  { id: 'probes', label: 'Probes', from: '0.3.0', what: 'Probes and their minimum RTT' },
  { id: 'raw', label: 'Raw' },
  { id: 'history', label: 'History', from: '0.5.0', what: 'Past runs and their differences' },
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
          <li>Measurements and multilateration arrive in v0.3.0.</li>
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
        <span className="text-fg-muted">Reading the passive sources. Findings appear as they arrive.</span>
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

function Summary({ report, running }) {
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
          These are passive answers. Measurements that confirm or rule them out arrive in v0.3.0.
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

export default function SidePanel({ run, targetIndex, onTargetChange }) {
  const [tab, setTab] = useState('summary')
  const current = TABS.find((t) => t.id === tab)
  const report = run?.targets?.[targetIndex] ?? null
  const running = run?.status === 'running'

  let body
  if (current.from) {
    body = (
      <p className="border border-dashed border-line px-1 py-6 text-center font-mono text-xs text-fg-subtle">
        {current.what} will appear here from v{current.from}.
      </p>
    )
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
    body = tab === 'summary' ? <Summary report={report} running={running} /> : <Evidence report={report} />
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
