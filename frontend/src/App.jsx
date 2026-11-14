import { useCallback, useEffect, useState } from 'react'
import { fetchMeta, subscribeEvents } from './lib/api.js'
import TopBar from './components/layout/TopBar.jsx'
import LogPane from './components/layout/LogPane.jsx'
import MapView from './components/map/MapView.jsx'
import SidePanel from './components/panel/SidePanel.jsx'

let nextLogId = 0
const now = () => new Date().toLocaleTimeString('en-GB', { hour12: false })

export default function App() {
  const [meta, setMeta] = useState(null)
  const [basemap, setBasemap] = useState('carto')
  const [connection, setConnection] = useState('connecting')
  const [entries, setEntries] = useState([])

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
    return () => controller.abort()
  }, [log])

  useEffect(
    () =>
      subscribeEvents(
        { ready: (data) => log('ok', 'server', `Connected to the local server (v${data.version})`) },
        setConnection,
      ),
    [log],
  )

  return (
    <div data-app-ready className="grid h-full grid-rows-[auto_minmax(0,1fr)_auto] max-[860px]:h-auto">
      <TopBar version={meta?.version} />
      <main className="grid min-h-0 grid-cols-[minmax(0,1fr)_400px] max-[1100px]:grid-cols-[minmax(0,1fr)_340px] max-[860px]:grid-cols-1">
        <section className="relative min-h-0 bg-ocean max-[860px]:h-[380px]">
          <MapView
            basemap={basemap}
            cartoStyleUrl={meta?.basemaps?.carto?.style}
            onBasemapChange={setBasemap}
            onLog={log}
          />
        </section>
        <SidePanel />
      </main>
      <LogPane entries={entries} connection={connection} />
    </div>
  )
}
