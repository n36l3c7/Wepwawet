import { useEffect, useRef, useState } from 'react'
import { fetchSettings, removeKey, saveKey, saveSettings } from '../../lib/api.js'
import { OPTION_GROUPS, changedValues, keyState } from '../../lib/settings.js'

const inputClass = 'border border-line bg-canvas px-1.5 py-0.5 font-mono text-[12px] focus:border-accent outline-none'
const buttonClass =
  'border border-line bg-raised px-2.5 py-0.5 font-mono text-[11.5px] hover:border-accent disabled:cursor-not-allowed disabled:opacity-40'

function Section({ title, children }) {
  return (
    <section className="grid gap-2">
      <h3 className="font-mono text-[10.5px] uppercase tracking-widest text-fg-subtle">{title}</h3>
      {children}
    </section>
  )
}

function KeyRow({ entry, busy, onSave, onRemove }) {
  const [value, setValue] = useState('')
  const id = `key-${entry.name}`
  const fromEnv = entry.source === 'env'
  return (
    <form
      className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b border-line-soft pb-2"
      onSubmit={(event) => {
        event.preventDefault()
        onSave(entry.name, value).then((ok) => ok && setValue(''))
      }}
    >
      <label htmlFor={id} className="grid gap-0.5">
        <span className="font-mono text-[12px]">{entry.name}</span>
        <span className="text-[11.5px] text-fg-muted">{entry.description}</span>
      </label>
      <span
        className={['justify-self-end font-mono text-[11px]', entry.source ? 'text-good' : 'text-fg-subtle'].join(' ')}
      >
        {keyState(entry)}
      </span>
      <div className="col-span-2 flex gap-1.5">
        <input
          id={id}
          type="password"
          autoComplete="off"
          spellCheck="false"
          placeholder={fromEnv ? 'set in the environment' : entry.source ? 'replace the stored key' : 'paste the key'}
          disabled={fromEnv || busy}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className={`${inputClass} min-w-0 flex-1`}
        />
        <button type="submit" className={buttonClass} disabled={fromEnv || busy || !value.trim()}>
          save
        </button>
        {entry.source === 'file' ? (
          <button type="button" className={buttonClass} disabled={busy} onClick={() => onRemove(entry.name)}>
            remove
          </button>
        ) : null}
      </div>
    </form>
  )
}

function OptionInput({ option, value, locked, onChange }) {
  const id = `opt-${option.section}-${option.key}`
  let input
  if (option.type === 'bool') {
    input = (
      <input
        id={id}
        type="checkbox"
        checked={Boolean(value)}
        disabled={locked}
        onChange={(event) => onChange(event.target.checked)}
        className="m-0 justify-self-end accent-[var(--color-accent)]"
      />
    )
  } else if (option.choices) {
    input = (
      <select
        id={id}
        value={value ?? ''}
        disabled={locked}
        onChange={(e) => onChange(e.target.value)}
        className={inputClass}
      >
        {option.choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    )
  } else {
    input = (
      <input
        id={id}
        type="number"
        min={option.min}
        max={option.max}
        value={value ?? ''}
        disabled={locked}
        onChange={(event) => onChange(event.target.value === '' ? '' : Number(event.target.value))}
        className={`${inputClass} w-[90px] justify-self-end tabular-nums`}
      />
    )
  }
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
      <label htmlFor={id} className="grid gap-0.5 text-[12.5px]">
        {option.label}
        {option.help ? <span className="text-[11px] text-fg-muted">{option.help}</span> : null}
        {locked ? <span className="text-[11px] text-warn">fixed by an environment variable</span> : null}
      </label>
      {input}
    </div>
  )
}

/** Settings window: API keys, the main options and the sources. Keys are write-only. */
export default function SettingsDialog({ open, onClose, onChanged }) {
  const dialogRef = useRef(null)
  const [view, setView] = useState(null)
  const [values, setValues] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  useEffect(() => {
    if (!open) return undefined
    const controller = new AbortController()
    setMessage(null)
    fetchSettings({ signal: controller.signal })
      .then((data) => {
        setView(data)
        setValues(data.values)
      })
      .catch((error) => {
        if (!controller.signal.aborted) setMessage({ tone: 'error', text: error.message })
      })
    return () => controller.abort()
  }, [open])

  const apply = async (action, success) => {
    setBusy(true)
    setMessage(null)
    try {
      const data = await action()
      setView(data)
      setValues(data.values)
      setMessage({ tone: 'ok', text: success })
      onChanged?.(data)
      return true
    } catch (error) {
      setMessage({ tone: 'error', text: error.message })
      return false
    } finally {
      setBusy(false)
    }
  }

  const changes = view && values ? changedValues(view.values, values) : {}
  const dirty = Object.keys(changes).length > 0
  const needsRestart = Object.entries(changes).some(([section, keys]) =>
    Object.keys(keys).some((key) => view.restart_required.includes(`${section}.${key}`)),
  )
  const setOption = (section, key, value) => setValues((v) => ({ ...v, [section]: { ...v[section], [key]: value } }))

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      aria-labelledby="settings-title"
      className="m-auto max-h-[88vh] w-[min(760px,calc(100vw-32px))] border border-line bg-sunken p-0 text-fg backdrop:bg-black/60"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h2 id="settings-title" className="font-mono text-[13px] font-bold tracking-wide">
          settings
        </h2>
        <button type="button" onClick={onClose} className={buttonClass} aria-label="Close settings">
          close
        </button>
      </div>
      {!view || !values ? (
        <p className="p-4 font-mono text-[12px] text-fg-muted">{message?.text ?? 'Loading…'}</p>
      ) : (
        <div className="grid max-h-[calc(88vh-100px)] gap-5 overflow-y-auto p-4">
          <Section title="API keys">
            <p className="text-[11.5px] text-fg-muted">
              Stored in <code className="font-mono">{view.files.secrets}</code>, readable by you only. A key is never
              shown again once saved.
            </p>
            {view.keys.map((entry) => (
              <KeyRow
                key={entry.name}
                entry={entry}
                busy={busy}
                onSave={(name, value) => apply(() => saveKey(name, value), `Saved the ${name} key.`)}
                onRemove={(name) => apply(() => removeKey(name), `Removed the ${name} key.`)}
              />
            ))}
          </Section>
          {OPTION_GROUPS.map((group) => (
            <Section key={group.title} title={group.title}>
              {group.options.map((option) => (
                <OptionInput
                  key={`${option.section}.${option.key}`}
                  option={option}
                  value={values[option.section]?.[option.key]}
                  locked={view.env_overrides.includes(`${option.section}.${option.key}`)}
                  onChange={(value) => setOption(option.section, option.key, value)}
                />
              ))}
            </Section>
          ))}
          <Section title="Sources">
            <div className="grid grid-cols-2 gap-x-6 gap-y-1 max-[560px]:grid-cols-1">
              {view.sources.map((source) => (
                <label key={source.name} className="flex items-center gap-2 text-[12.5px]">
                  <input
                    type="checkbox"
                    checked={values.providers?.[source.name]?.enabled ?? true}
                    onChange={(event) => setOption('providers', source.name, { enabled: event.target.checked })}
                    className="m-0 accent-[var(--color-accent)]"
                  />
                  {source.label}
                  <span className="font-mono text-[10.5px] text-fg-subtle">{source.kind}</span>
                </label>
              ))}
            </div>
          </Section>
          <p className="text-[11.5px] text-fg-muted">
            Options are saved in <code className="font-mono">{view.files.settings}</code>, over your config.toml.
          </p>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-end gap-3 border-t border-line px-4 py-2.5">
        {message ? (
          <p role="status" className={`mr-auto text-[12px] ${message.tone === 'error' ? 'text-high' : 'text-good'}`}>
            {message.text}
          </p>
        ) : null}
        {needsRestart ? (
          <span className="text-[11.5px] text-warn">Window rendering applies at the next start.</span>
        ) : null}
        <button type="button" className={buttonClass} disabled={!dirty || busy} onClick={() => setValues(view.values)}>
          undo
        </button>
        <button
          type="button"
          disabled={!dirty || busy}
          onClick={() => apply(() => saveSettings(changes), 'Settings saved.')}
          className="bg-accent px-3 py-0.5 font-mono text-[11.5px] font-bold text-accent-ink disabled:cursor-not-allowed disabled:opacity-40"
        >
          save
        </button>
      </div>
    </dialog>
  )
}
