import { useEffect, useRef } from 'react'

const STATE_LABEL = {
  connecting: { text: 'connecting', className: 'text-fg-subtle' },
  open: { text: '● live · sse', className: 'text-good' },
  reconnecting: { text: 'reconnecting', className: 'text-warn' },
}

const LEVEL_CLASS = { info: '', error: 'text-high', ok: 'text-accent-fg' }

export default function LogPane({ entries, connection }) {
  const ref = useRef(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [entries])
  const state = STATE_LABEL[connection] ?? STATE_LABEL.connecting

  return (
    <footer
      ref={ref}
      className="h-32 overflow-auto border-t border-line bg-sunken px-3 pb-2 font-mono text-[11.5px]"
      aria-label="Log"
      aria-live="polite"
    >
      <div className="sticky top-0 mb-1 flex justify-between bg-sunken pt-1.5 text-[10.5px] uppercase tracking-widest text-fg-subtle">
        <span>log</span>
        <span className={state.className}>{state.text}</span>
      </div>
      {entries.map((entry) => (
        <div
          key={entry.id}
          className="grid grid-cols-[6.5em_7.5em_minmax(0,1fr)] gap-2.5 max-[860px]:grid-cols-[6.5em_minmax(0,1fr)]"
        >
          <span className="tabular-nums text-fg-subtle">{entry.time}</span>
          <span
            className={`${entry.level === 'error' ? 'text-high' : 'text-accent2'} max-[860px]:hidden`}
          >{`[${entry.source}]`}</span>
          <span className={LEVEL_CLASS[entry.level] ?? ''}>{entry.message}</span>
        </div>
      ))}
    </footer>
  )
}
