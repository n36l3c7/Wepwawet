import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  cancelBulk,
  cancelRun,
  fetchBudget,
  fetchMeta,
  fetchProviders,
  fetchRun,
  parseTargets,
  startBulk,
  startRun,
  subscribeEvents,
} from './lib/api.js'
import { reportPoints } from './lib/overlays.js'
import TopBar from './components/layout/TopBar.jsx'
import LogPane from './components/layout/LogPane.jsx'
import MapView from './components/map/MapView.jsx'
import SidePanel from './components/panel/SidePanel.jsx'
import SettingsDialog from './components/settings/SettingsDialog.jsx'

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
  const [budgets, setBudgets] = useState([])
  const [bulk, setBulk] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const snapshotRef = useRef(null)

  const refreshBudget = useCallback(() => {
    fetchBudget()
      .then(setBudgets)
      .catch(() => setBudgets([]))
  }, [])

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
            if (data.status !== 'running') refreshBudget()
          },
          bulk: (data) => {
            setBulk((current) => (current && current.id !== data.id ? current : data))
            if (data.status !== 'running') refreshBudget()
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
    [log, refreshBudget],
  )

  useEffect(() => refreshBudget(), [refreshBudget])

  // Keys and sources changed in the settings: the chips and the quota follow.
  const onSettingsChanged = useCallback(() => {
    fetchProviders()
      .then(setProviders)
      .catch((error) => log('error', 'api', `Cannot list the data sources: ${error.message}`))
    refreshBudget()
  }, [log, refreshBudget])

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

  const onBulk = useCallback(
    async (file, maxTests) => {
      setInputError(null)
      try {
        const queries = parseTargets(await file.text())
        if (!queries.length) throw new Error(`${file.name} has no targets (one per line)`)
        const job = await startBulk(queries, maxTests)
        setBulk(job)
        log('info', 'bulk', `Bulk run of ${queries.length} targets, up to ${maxTests} tests`)
      } catch (error) {
        setInputError(error.message)
      }
    },
    [log],
  )

  const onOpenRun = useCallback(
    async (id) => {
      try {
        setRun(await fetchRun(id))
        setTargetIndex(0)
      } catch (error) {
        log('error', 'history', `Cannot open the run: ${error.message}`)
      }
    },
    [log],
  )

  const onSnapshot = useCallback(async () => {
    try {
      const blob = await snapshotRef.current?.()
      if (!blob) throw new Error('the map is not ready')
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = `wepwawet-${run?.query ?? 'map'}.png`.replace(/[^\w.-]+/g, '_')
      link.click()
      setTimeout(() => URL.revokeObjectURL(link.href), 10_000)
    } catch (error) {
      log('error', 'export', `Cannot save the map: ${error.message}`)
    }
  }, [run, log])

  const onStop = useCallback(async () => {
    if (bulk?.status === 'running') {
      try {
        await cancelBulk(bulk.id)
      } catch (error) {
        log('error', 'bulk', `Cannot cancel: ${error.message}`)
      }
      return
    }
    if (!run) return
    try {
      await cancelRun(run.id)
      log('info', 'run', 'Run cancelled')
    } catch (error) {
      log('error', 'run', `Cannot cancel: ${error.message}`)
    }
  }, [run, bulk, log])

  const report = run?.targets?.[targetIndex] ?? null
  const points = useMemo(() => reportPoints(report), [report])

  return (
    <div data-app-ready className="grid h-full grid-rows-[auto_minmax(0,1fr)_auto] max-[860px]:h-auto">
      <TopBar
        version={meta?.version}
        running={run?.status === 'running' || bulk?.status === 'running'}
        providers={providers}
        budgets={budgets}
        error={inputError}
        onRun={onRun}
        onStop={onStop}
        onBulk={onBulk}
        onSettings={() => setSettingsOpen(true)}
      />
      <main className="grid min-h-0 grid-cols-[minmax(0,1fr)_400px] max-[1100px]:grid-cols-[minmax(0,1fr)_340px] max-[860px]:grid-cols-1">
        <section className="relative min-h-0 bg-ocean max-[860px]:h-[380px]">
          <MapView
            basemap={basemap}
            cartoStyleUrl={meta?.basemaps?.carto?.style}
            report={bulk ? null : report}
            points={bulk ? [] : points}
            bulkItems={bulk?.items ?? null}
            onSnapshotReady={(fn) => {
              snapshotRef.current = fn
            }}
            onBasemapChange={setBasemap}
            onLog={log}
          />
        </section>
        <SidePanel
          run={run}
          targetIndex={targetIndex}
          onTargetChange={setTargetIndex}
          onRerun={onRun}
          onSnapshot={onSnapshot}
          onOpenRun={(id) => {
            setBulk((current) => (current?.status === 'running' ? current : null))
            onOpenRun(id)
          }}
          bulk={bulk}
          onCancelBulk={onStop}
          onCloseBulk={() => setBulk(null)}
        />
      </main>
      <LogPane entries={entries} connection={connection} />
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} onChanged={onSettingsChanged} />
    </div>
  )
}
