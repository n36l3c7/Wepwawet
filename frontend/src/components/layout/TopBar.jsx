import { MicroJackal } from '../ui/AsciiJackal.jsx'

export default function TopBar({ version }) {
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-sunken px-3.5 py-2">
      <div className="flex items-baseline gap-2 font-mono">
        <MicroJackal className="min-w-[4ch] text-[15px] font-bold text-accent" />
        <span className="text-[15px] font-bold tracking-wide">wepwawet</span>
        <span className="text-[11px] text-fg-subtle">{version ? `v${version}` : ''}</span>
      </div>
      <form
        className="flex min-w-0 flex-[1_1_380px] items-stretch border border-line bg-panel"
        onSubmit={(event) => event.preventDefault()}
        aria-label="Target"
      >
        <span className="flex items-center pl-2.5 pr-1 font-mono text-accent" aria-hidden="true">
          ❯
        </span>
        <input
          id="target"
          name="target"
          disabled
          aria-describedby="target-note"
          placeholder="IP address or hostname"
          className="min-w-0 flex-1 bg-transparent px-1.5 py-1.5 font-mono text-[13px] outline-none placeholder:text-fg-subtle disabled:cursor-not-allowed"
        />
        <button
          type="submit"
          disabled
          className="bg-accent px-4 font-mono text-[12px] font-bold tracking-wider text-accent-ink disabled:cursor-not-allowed disabled:opacity-40"
        >
          ▶ RUN
        </button>
      </form>
      <p id="target-note" className="font-mono text-[11px] text-fg-subtle">
        Locating targets arrives in v0.2.0
      </p>
    </header>
  )
}
