import { useState } from 'react'
import { MicroJackal } from '../ui/AsciiJackal.jsx'

function ProviderChip({ provider }) {
  const ready = !provider.reason
  return (
    <span
      title={ready ? `${provider.label}: ready` : `${provider.label}: ${provider.reason}`}
      className={[
        'inline-flex items-center gap-1.5 border border-line bg-panel px-2 py-0.5 font-mono text-[11px]',
        ready ? 'text-fg' : 'text-fg-subtle',
      ].join(' ')}
    >
      <i
        aria-hidden="true"
        className={[
          'inline-block h-[7px] w-[7px] rounded-full',
          ready ? 'bg-accent shadow-[0_0_6px_var(--color-accent)]' : 'border border-warn',
        ].join(' ')}
      />
      {provider.label}
      <span className="sr-only">{ready ? 'ready' : `unavailable: ${provider.reason}`}</span>
    </span>
  )
}

/** Ready sources as chips; the unavailable ones folded into one chip that lists why. */
function SourceSummary({ providers }) {
  const ready = providers.filter((p) => !p.reason)
  const off = providers.filter((p) => p.reason)
  return (
    <div className="flex flex-wrap gap-1.5" aria-label="Data sources">
      {ready.map((p) => (
        <ProviderChip key={p.name} provider={p} />
      ))}
      {off.length ? (
        <details className="relative">
          <summary className="cursor-pointer list-none border border-line bg-panel px-2 py-0.5 font-mono text-[11px] text-fg-subtle hover:text-fg">
            +{off.length} off
          </summary>
          <ul className="absolute right-0 z-20 mt-1 grid w-[340px] gap-1.5 border border-line bg-sunken p-2.5 text-[12px] shadow-lg">
            {off.map((p) => (
              <li key={p.name} className="grid gap-0.5">
                <b className="font-mono text-[11.5px] font-medium">{p.label}</b>
                <span className="text-fg-muted">{p.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  )
}

function BudgetMeter({ budget }) {
  if (budget.remaining === null || budget.limit === null) return null
  const used = Math.max(0, budget.limit - budget.remaining)
  const share = budget.limit ? Math.min(100, (used / budget.limit) * 100) : 0
  const tone = share > 85 ? 'bg-high' : share > 60 ? 'bg-warn' : 'bg-accent'
  const label = budget.provider === 'globalping' ? 'Globalping / hour' : 'RIPE Atlas credits'
  return (
    <div
      className="grid min-w-[170px] gap-0.5 font-mono text-[11px] text-fg-muted"
      title={`${budget.remaining} ${budget.unit} left`}
    >
      <div className="flex justify-between gap-2">
        <span>{label}</span>
        <span className="tabular-nums">{budget.remaining} left</span>
      </div>
      <div
        className="relative h-[5px] bg-line-soft"
        role="meter"
        aria-label={`${label} used`}
        aria-valuemin="0"
        aria-valuemax={budget.limit}
        aria-valuenow={used}
      >
        <span className={`absolute inset-y-0 left-0 ${tone}`} style={{ width: `${share}%` }} />
      </div>
    </div>
  )
}

export default function TopBar({
  version,
  running,
  providers,
  budgets = [],
  error,
  onRun,
  onStop,
  onBulk,
  onSettings,
}) {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState('single')
  const [file, setFile] = useState(null)
  const [maxTests, setMaxTests] = useState(500)
  const mood = running ? 'animated' : error ? 'x' : 'open'

  const submit = (event) => {
    event.preventDefault()
    if (running) onStop()
    else if (mode === 'bulk' && file) onBulk(file, maxTests)
    else if (mode === 'single' && query.trim()) onRun(query.trim())
  }

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-sunken px-3.5 py-2">
      <div className="flex items-baseline gap-2 font-mono">
        <MicroJackal mood={mood} className="min-w-[4ch] text-[15px] font-bold text-accent" />
        <span className="text-[15px] font-bold tracking-wide">wepwawet</span>
        <span className="text-[11px] text-fg-subtle">{version ? `v${version}` : ''}</span>
      </div>
      <form className="grid min-w-0 flex-[1_1_380px] gap-1" onSubmit={submit} aria-label="Target">
        <div
          className={[
            'flex min-w-0 items-stretch border bg-panel',
            error ? 'border-high' : 'border-line focus-within:border-accent',
          ].join(' ')}
        >
          <div className="flex border-r border-line font-mono text-[11.5px]" role="group" aria-label="Mode">
            {[
              ['single', 'ip/host'],
              ['bulk', 'file'],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={mode === key}
                disabled={running}
                onClick={() => setMode(key)}
                className={mode === key ? 'bg-accent-soft px-2.5 text-accent-fg' : 'px-2.5 text-fg-subtle'}
              >
                {label}
              </button>
            ))}
          </div>
          {mode === 'bulk' ? (
            <div className="flex min-w-0 flex-1 items-center gap-2 px-2 font-mono text-[12px]">
              <input
                id="bulk-file"
                type="file"
                accept=".txt,.csv,.list,text/plain"
                aria-label="File with one target per line"
                disabled={running}
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="min-w-0 flex-1 text-fg-muted file:mr-2 file:border file:border-line file:bg-raised file:px-2 file:py-0.5 file:font-mono file:text-fg"
              />
              <label htmlFor="bulk-tests" className="whitespace-nowrap text-fg-subtle">
                tests
              </label>
              <input
                id="bulk-tests"
                type="number"
                min="0"
                step="10"
                value={maxTests}
                onChange={(event) => setMaxTests(Number(event.target.value))}
                className="w-[72px] border border-line bg-canvas px-1.5 py-0.5 tabular-nums"
              />
            </div>
          ) : (
            <>
              <span className="flex items-center pl-2.5 pr-1 font-mono text-accent" aria-hidden="true">
                ❯
              </span>
              <input
                id="target"
                name="target"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                disabled={running}
                autoComplete="off"
                spellCheck="false"
                aria-invalid={Boolean(error)}
                aria-describedby={error ? 'target-error' : undefined}
                placeholder="IP address or hostname, e.g. one.one.one.one"
                className="min-w-0 flex-1 bg-transparent px-1.5 py-1.5 font-mono text-[13px] outline-none placeholder:text-fg-subtle disabled:text-fg-muted"
              />
            </>
          )}
          <button
            type="submit"
            disabled={!running && (mode === 'bulk' ? !file : !query.trim())}
            className={[
              'px-4 font-mono text-[12px] font-bold tracking-wider disabled:cursor-not-allowed disabled:opacity-40',
              running ? 'bg-high-soft text-high' : 'bg-accent text-accent-ink',
            ].join(' ')}
          >
            {running ? '■ STOP' : '▶ RUN'}
          </button>
        </div>
        {error ? (
          <p id="target-error" role="alert" className="font-mono text-[11.5px] text-high">
            {error}
          </p>
        ) : null}
      </form>
      <SourceSummary providers={providers} />
      {budgets.map((b) => (
        <BudgetMeter key={b.provider} budget={b} />
      ))}
      <button
        type="button"
        onClick={onSettings}
        title="Settings: API keys, options and sources"
        className="ml-auto border border-line bg-panel px-2 py-0.5 font-mono text-[11px] text-fg-muted hover:border-accent hover:text-fg"
      >
        ⚙ settings
      </button>
    </header>
  )
}
