import { useCallback, useEffect, useMemo, useState } from 'react'
import { cancelRun, fetchMeta, fetchProviders, startRun, subscribeEvents } from './lib/api.js'
import { reportPoints } from './lib/overlays.js'
import TopBar from './components/layout/TopBar.jsx'
import LogPane from './components/layout/LogPane.jsx'
import MapView from './components/map/MapView.jsx'
import SidePanel from './components/panel/SidePanel.jsx'

let nextLogId = 0
const now = () => new Date().toLocaleTimeString('en-GB', { hour12: false })

export default function App() {
  const [meta, setMeta] = useState(null)
  const [providers, setProviders] = useState([])
  const [basemap, setBasemap] = useState('carto')
  const [connection, setConnection] = useState('connecting')
  const [entries, setEntries] = useState([])
  const [run, setRun] = useState(null)
  const [targetIndex, setTargetIndex] = useState(0)
  const [inputError, setInputError] = useState(null)

  const log = useCallback((level, source, message) => {
    setEntries((list) => [...list.slice(-499), { id: nextLogId++, time: now(), level, source, message }])
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchMeta({ signal: controller.signal })
      .then((data) => {
        setMeta(data)
        setBasemap(data.basemap)
      })
      .catch((error) => {
        if (!controller.signal.aborted) log('error', 'api', `Cannot read the server settings: ${error.message}`)
      })
    fetchProviders({ signal: controller.signal })
      .then(setProviders)
      .catch((error) => {
        if (!controller.signal.aborted) log('error', 'api', `Cannot list the data sources: ${error.message}`)
      })
    return () => controller.abort()
  }, [log])

  useEffect(
    () =>
      subscribeEvents(
        {
          ready: (data) => log('ok', 'server', `Connected to the local server (v${data.version})`),
          log: (data) => log(data.level, data.source, data.message),
          run: (data) => {
            setRun((current) => (current && current.id !== data.id ? current : data))
            if (data.status === 'failed') setInputError(data.error)
          },
          target: (data) =>
            setRun((current) => {
              if (!current || current.id !== data.run) return current
              const targets = [...(current.targets ?? [])]
              targets[data.index] = data.report
              return { ...current, targets }
            }),
        },
        setConnection,
      ),
    [log],
  )

  const onRun = useCallback(
    async (query) => {
      setInputError(null)
      setTargetIndex(0)
      try {
        const started = await startRun(query)
        setRun(started)
        log('info', 'run', `Locating ${query}`)
      } catch (error) {
        setInputError(error.message)
      }
    },
    [log],
  )

  const onStop = useCallback(async () => {
    if (!run) return
    try {
      await cancelRun(run.id)
      log('info', 'run', 'Run cancelled')
    } catch (error) {
      log('error', 'run', `Cannot cancel: ${error.message}`)
    }
  }, [run, log])

  const report = run?.targets?.[targetIndex] ?? null
  const points = useMemo(() => reportPoints(report), [report])

  return (
    <div data-app-ready className="grid h-full grid-rows-[auto_minmax(0,1fr)_auto] max-[860px]:h-auto">
      <TopBar
        version={meta?.version}
        running={run?.status === 'running'}
        providers={providers}
        error={inputError}
        onRun={onRun}
        onStop={onStop}
      />
      <main className="grid min-h-0 grid-cols-[minmax(0,1fr)_400px] max-[1100px]:grid-cols-[minmax(0,1fr)_340px] max-[860px]:grid-cols-1">
        <section className="relative min-h-0 bg-ocean max-[860px]:h-[380px]">
          <MapView
            basemap={basemap}
            cartoStyleUrl={meta?.basemaps?.carto?.style}
            points={points}
            onBasemapChange={setBasemap}
            onLog={log}
          />
        </section>
        <SidePanel run={run} targetIndex={targetIndex} onTargetChange={setTargetIndex} />
      </main>
      <LogPane entries={entries} connection={connection} />
    </div>
  )
}
