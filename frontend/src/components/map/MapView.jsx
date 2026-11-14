import { useEffect, useRef, useState } from 'react'
import { Map as MapLibreMap, Marker, NavigationControl, setWorkerUrl } from 'maplibre-gl'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { BASEMAPS, offlineStyle, readPalette, themeCartoStyle } from '../../lib/basemaps.js'
import { POINT_KINDS, accuracyCircles, frameBounds } from '../../lib/overlays.js'
import AsciiJackal from '../ui/AsciiJackal.jsx'

// The bundled build cannot find MapLibre's worker next to itself; point it to Vite's copy.
setWorkerUrl(maplibreWorkerUrl)

const INITIAL_VIEW = { center: [12, 30], zoom: 1.6 }
const CIRCLES_SOURCE = 'wpw-accuracy'

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

/** Accuracy circles live in the style, so they are added again after every style change. */
function drawCircles(map, circles) {
  if (!map.isStyleLoaded()) return
  const palette = readPalette()
  const source = map.getSource(CIRCLES_SOURCE)
  if (source) {
    source.setData(circles)
    return
  }
  map.addSource(CIRCLES_SOURCE, { type: 'geojson', data: circles })
  map.addLayer({
    id: `${CIRCLES_SOURCE}-fill`,
    type: 'fill',
    source: CIRCLES_SOURCE,
    paint: { 'fill-color': palette.accent2, 'fill-opacity': 0.06 },
  })
  map.addLayer({
    id: `${CIRCLES_SOURCE}-line`,
    type: 'line',
    source: CIRCLES_SOURCE,
    paint: { 'line-color': palette.accent2, 'line-opacity': 0.55, 'line-width': 1, 'line-dasharray': [3, 2] },
  })
}

/** HTML markers survive style changes and need no glyph server (the offline map has none). */
function markerElement(point) {
  const element = document.createElement('div')
  element.className = `wpw-marker wpw-marker-${point.kind}`
  element.title = point.title
  element.setAttribute('role', 'img')
  element.setAttribute('aria-label', `${POINT_KINDS[point.kind]?.label ?? 'Point'}: ${point.title}`)
  const shape = document.createElement('i')
  shape.className = 'wpw-shape'
  element.appendChild(shape)
  if (point.kind !== 'facility') {
    const label = document.createElement('span')
    label.textContent = point.label
    element.appendChild(label)
  }
  return element
}

export default function MapView({ basemap, cartoStyleUrl, points, onBasemapChange, onLog }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const circlesRef = useRef(accuracyCircles([]))
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
    map.on('style.load', () => drawCircles(map, circlesRef.current))
    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // Switch base map. Wait for the server settings before loading CARTO, or the offline map would flash first.
  useEffect(() => {
    const map = mapRef.current
    if (!map || (basemap === 'carto' && !cartoStyleUrl)) return undefined
    const controller = new AbortController()
    loadStyle(basemap, cartoStyleUrl, readPalette(), controller.signal)
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

  // Points and circles of the current report.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return undefined
    circlesRef.current = accuracyCircles(points)
    drawCircles(map, circlesRef.current)
    const ordered = [...points].sort((a, b) => (POINT_KINDS[b.kind]?.order ?? 0) - (POINT_KINDS[a.kind]?.order ?? 0))
    const markers = ordered.map((point) =>
      new Marker({ element: markerElement(point), anchor: 'center' }).setLngLat([point.lon, point.lat]).addTo(map),
    )
    const bounds = frameBounds(points)
    if (bounds) map.fitBounds(bounds, { padding: 80, maxZoom: 8, duration: 800 })
    return () => markers.forEach((marker) => marker.remove())
  }, [points])

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
      {points.length ? (
        <div className="absolute left-2.5 top-2.5 grid gap-1 border border-line bg-sunken/90 px-2.5 py-1.5 font-mono text-[10.5px] text-fg-muted">
          {Object.entries(POINT_KINDS).map(([kind, { label }]) => (
            <span key={kind} className="flex items-center gap-2">
              <span aria-hidden="true" className={`wpw-marker wpw-marker-${kind} wpw-legend`}>
                <i className="wpw-shape" />
              </span>
              {label}
            </span>
          ))}
        </div>
      ) : null}
      <div className="pointer-events-none absolute bottom-2.5 left-2.5 border border-line bg-sunken/90 px-2 py-1 font-mono text-[10.5px] tabular-nums text-fg-muted">
        {cursor ? `${cursor.lat.toFixed(4)}, ${cursor.lng.toFixed(4)}` : 'lat, lon'}
      </div>
    </div>
  )
}
