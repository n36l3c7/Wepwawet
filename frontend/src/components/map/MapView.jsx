import { useEffect, useRef, useState } from 'react'
import { Map as MapLibreMap, NavigationControl, setWorkerUrl } from 'maplibre-gl'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { BASEMAPS, offlineStyle, readPalette, themeCartoStyle } from '../../lib/basemaps.js'
import AsciiJackal from '../ui/AsciiJackal.jsx'

// The bundled build cannot find MapLibre's worker next to itself; point it to Vite's copy.
setWorkerUrl(maplibreWorkerUrl)

const INITIAL_VIEW = { center: [12, 30], zoom: 1.6 }

// Natural Earth 1:50m (~750 kB): a separate chunk, loaded only for the offline map.
const loadCountries = () => import('world-atlas/countries-50m.json').then((module) => module.default)

const blankStyle = (palette) => ({
  version: 8,
  sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': palette.ocean } }],
})

/** Build the MapLibre style for `basemap`. CARTO failures fall back to the offline map. */
async function loadStyle(basemap, cartoStyleUrl, palette, signal) {
  if (basemap === 'carto') {
    const response = await fetch(cartoStyleUrl, { signal })
    if (!response.ok) throw new Error(`CARTO style request failed (HTTP ${response.status})`)
    return themeCartoStyle(await response.json(), palette)
  }
  return offlineStyle(await loadCountries(), palette)
}

export default function MapView({ basemap, cartoStyleUrl, onBasemapChange, onLog }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const [webglError, setWebglError] = useState(null)
  const [cursor, setCursor] = useState(null)

  // Create the map once.
  useEffect(() => {
    let map
    try {
      map = new MapLibreMap({
        container: containerRef.current,
        style: blankStyle(readPalette()),
        ...INITIAL_VIEW,
        attributionControl: { compact: true },
        renderWorldCopies: false,
      })
    } catch (error) {
      setWebglError(error.message || 'WebGL is not available')
      return undefined
    }
    map.addControl(new NavigationControl({ showCompass: false }), 'bottom-right')
    map.on('mousemove', (event) => setCursor(event.lngLat))
    map.on('mouseout', () => setCursor(null))
    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // Switch base map.
  useEffect(() => {
    const map = mapRef.current
    // Wait for the server settings before loading CARTO, or the offline map would flash first.
    if (!map || (basemap === 'carto' && !cartoStyleUrl)) return undefined
    const controller = new AbortController()
    const palette = readPalette()
    loadStyle(basemap, cartoStyleUrl, palette, controller.signal)
      .then((style) => {
        // A newer selection may have replaced this one while it loaded.
        if (!controller.signal.aborted) map.setStyle(style, { diff: false })
      })
      .catch((error) => {
        if (controller.signal.aborted) return
        onLog?.('error', 'map', `${error.message}. Showing the offline map instead.`)
        onBasemapChange?.('offline')
      })
    return () => controller.abort()
  }, [basemap, cartoStyleUrl, onBasemapChange, onLog])

  if (webglError) {
    return (
      <div className="grid h-full place-items-center bg-ocean p-6">
        <div className="flex max-w-md flex-col items-center gap-4 text-center">
          <AsciiJackal mood="x" tone="muted" />
          <p className="font-mono text-sm text-high">The map cannot start: {webglError}.</p>
          <p className="text-fg-muted">
            This display has no working WebGL. Open Wepwawet in your browser instead:{' '}
            <code className="font-mono text-accent-fg">wepwawet gui --browser</code>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="relative h-full">
      {/* h-full, not absolute: maplibre-gl.css forces position: relative on the container. */}
      <div ref={containerRef} className="h-full w-full" aria-label="Map" role="region" />
      <div
        className="absolute right-2.5 top-2.5 flex border border-line bg-sunken/90 font-mono text-[11px]"
        role="group"
        aria-label="Base map"
      >
        {Object.entries(BASEMAPS).map(([key, { label }]) => (
          <button
            key={key}
            type="button"
            aria-pressed={basemap === key}
            onClick={() => onBasemapChange?.(key)}
            className={[
              'px-2.5 py-1 not-first:border-l not-first:border-line',
              basemap === key ? 'bg-accent-soft text-accent-fg' : 'text-fg-muted hover:bg-raised hover:text-fg',
            ].join(' ')}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="pointer-events-none absolute bottom-2.5 left-2.5 border border-line bg-sunken/90 px-2 py-1 font-mono text-[10.5px] tabular-nums text-fg-muted">
        {cursor ? `${cursor.lat.toFixed(4)}, ${cursor.lng.toFixed(4)}` : 'lat, lon'}
      </div>
    </div>
  )
}
