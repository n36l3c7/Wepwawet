import { useState } from 'react'
import AsciiJackal from '../ui/AsciiJackal.jsx'

const TABS = [
  { id: 'summary', label: 'Summary' },
  { id: 'evidence', label: 'Evidence', from: '0.2.0', what: 'Evidence from each passive source' },
  { id: 'hops', label: 'Hops', from: '0.4.0', what: 'Traceroute hops with PTR, ASN and location hints' },
  { id: 'probes', label: 'Probes', from: '0.3.0', what: 'Probes and their minimum RTT' },
  { id: 'raw', label: 'Raw', from: '0.2.0', what: 'The raw JSON of a run' },
  { id: 'history', label: 'History', from: '0.5.0', what: 'Past runs and their differences' },
]

const LIMITS = [
  "Residential: the best answer is the ISP's PoP or BNG city.",
  'Mobile and CGNAT: the gateway can be hundreds of km from the user.',
  'VPN, Tor, proxies, hosting: you locate the exit server.',
  'Anycast: there is no single location.',
]

function Summary() {
  return (
    <>
      <div className="grid min-w-0 justify-items-center gap-3 pt-2 text-center">
        <AsciiJackal mood="closed" tone="muted" preset="medium" />
        <div className="grid min-w-0 gap-1">
          <b className="font-mono text-sm font-medium">Nothing to locate yet</b>
          <span className="text-fg-muted">
            This is the first release: the window, the map and the local server. Locating targets starts in v0.2.0.
          </span>
        </div>
      </div>
      <section className="grid gap-2">
        <h2 className="font-mono text-[10.5px] font-medium uppercase tracking-widest text-fg-subtle">
          What a run will do
        </h2>
        <ul className="grid list-disc gap-1 pl-4 text-fg-muted">
          <li>Read passive sources: GeoIP databases, RIPEstat, RDAP, reverse DNS, PeeringDB.</li>
          <li>Ping the target from many probes worldwide, then from probes near the best result.</li>
          <li>Intersect the speed-of-light disks and run traceroutes from the closest probes.</li>
        </ul>
      </section>
      <section className="grid gap-2">
        <h2 className="font-mono text-[10.5px] font-medium uppercase tracking-widest text-fg-subtle">Known limits</h2>
        <ul className="grid list-disc gap-1 pl-4 text-fg-muted">
          {LIMITS.map((limit) => (
            <li key={limit}>{limit}</li>
          ))}
        </ul>
      </section>
    </>
  )
}

export default function SidePanel() {
  const [tab, setTab] = useState('summary')
  const current = TABS.find((t) => t.id === tab)
  return (
    <aside className="flex min-h-0 min-w-0 flex-col border-l border-line bg-panel max-[860px]:border-l-0 max-[860px]:border-t">
      <div className="flex overflow-x-auto border-b border-line bg-sunken" role="tablist" aria-label="Details">
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
              'whitespace-nowrap border-b-2 px-3 pb-[7px] pt-2 font-mono text-[11.5px]',
              tab === t.id ? 'border-accent text-accent-fg' : 'border-transparent text-fg-muted hover:text-fg',
            ].join(' ')}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        id="panel-body"
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        className="grid flex-1 content-start gap-4 overflow-auto p-3.5"
      >
        {tab === 'summary' ? (
          <Summary />
        ) : (
          <p className="border border-dashed border-line px-1 py-6 text-center font-mono text-xs text-fg-subtle">
            {current.what} will appear here from v{current.from}.
          </p>
        )}
      </div>
    </aside>
  )
}
