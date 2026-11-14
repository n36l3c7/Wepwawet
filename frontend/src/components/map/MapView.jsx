import { useEffect, useRef, useState } from 'react'
import { Map as MapLibreMap, Marker, NavigationControl, Popup, setWorkerUrl } from 'maplibre-gl'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { BASEMAPS, offlineStyle, readPalette, themeCartoStyle } from '../../lib/basemaps.js'
import {
  POINT_KINDS,
  accuracyCircles,
  PATH_COLORS,
  diskFeatures,
  hopFeatures,
  pathFeatures,
  probeFeatures,
  regionFeatures,
  reportBounds,
} from '../../lib/overlays.js'
import AsciiJackal from '../ui/AsciiJackal.jsx'
import { confidenceTone } from '../panel/BulkPanel.jsx'

// The bundled build cannot find MapLibre's worker next to itself; point it to Vite's copy.
setWorkerUrl(maplibreWorkerUrl)

const INITIAL_VIEW = { center: [12, 30], zoom: 1.6 }

/** Toggleable layers, in drawing order (bottom first). */
export const LAYERS = [
  { id: 'region', label: 'Feasible region' },
  { id: 'disks', label: 'RTT disks', initiallyOff: true },
  { id: 'accuracy', label: 'GeoIP accuracy' },
  { id: 'paths', label: 'Traceroutes' },
  { id: 'probes', label: 'Probes' },
  { id: 'convergence', label: 'Convergence' },
  { id: 'estimate', label: 'Estimate' },
  { id: 'passive', label: 'Passive answers' },
  { id: 'facilities', label: 'PeeringDB facilities' },
]

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

const RTT_COLOR = (palette) => [
  'match',
  ['get', 'band'],
  1,
  palette.rtt1,
  2,
  palette.rtt2,
  3,
  palette.rtt3,
  4,
  palette.rtt4,
  5,
  palette.rtt5,
  palette.fgSubtle,
]

/** GeoJSON layers by toggle id: sources and MapLibre layer definitions. */
function layerSpecs(palette) {
  return {
    region: [
      {
        id: 'wpw-region-fill',
        type: 'fill',
        filter: ['==', ['get', 'kind'], 'region'],
        paint: { 'fill-color': palette.accent, 'fill-opacity': 0.22 },
      },
      {
        id: 'wpw-region-line',
        type: 'line',
        filter: ['==', ['get', 'kind'], 'region'],
        paint: { 'line-color': palette.accent, 'line-width': 1.2, 'line-opacity': 0.8 },
      },
    ],
    disks: [
      {
        id: 'wpw-disks',
        type: 'line',
        paint: { 'line-color': RTT_COLOR(palette), 'line-width': 1, 'line-opacity': 0.6, 'line-dasharray': [4, 3] },
      },
    ],
    accuracy: [
      { id: 'wpw-accuracy-fill', type: 'fill', paint: { 'fill-color': palette.accent2, 'fill-opacity': 0.06 } },
      {
        id: 'wpw-accuracy-line',
        type: 'line',
        paint: { 'line-color': palette.accent2, 'line-opacity': 0.55, 'line-width': 1, 'line-dasharray': [3, 2] },
      },
    ],
    probes: [
      {
        id: 'wpw-probes',
        type: 'circle',
        paint: {
          'circle-radius': ['case', ['==', ['get', 'band'], 0], 3, 4.5],
          'circle-color': RTT_COLOR(palette),
          'circle-stroke-color': palette.sunken,
          'circle-stroke-width': 1,
          'circle-opacity': ['case', ['==', ['get', 'band'], 0], 0.5, 1],
        },
      },
    ],
    paths: [
      {
        id: 'wpw-paths',
        type: 'line',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': PATH_COLOR(palette), 'line-width': 2, 'line-opacity': 0.85 },
      },
      {
        id: 'wpw-hops',
        type: 'circle',
        source: 'hops',
        paint: {
          'circle-radius': 3.5,
          'circle-color': palette.canvas,
          'circle-stroke-color': PATH_COLOR(palette),
          'circle-stroke-width': 1.5,
        },
      },
    ],
    estimate: [
      {
        id: 'wpw-uncertainty',
        type: 'line',
        filter: ['==', ['get', 'kind'], 'uncertainty'],
        paint: { 'line-color': palette.accentFg, 'line-width': 1.6 },
      },
    ],
  }
}

const SOURCE_OF = {
  region: 'region',
  estimate: 'region',
  disks: 'disks',
  accuracy: 'accuracy',
  probes: 'probes',
  paths: 'paths',
}

const PATH_COLOR = (palette) => [
  'match',
  ['get', 'color'],
  ...PATH_COLORS.flatMap((name) => [name, palette[name] || palette.accent]),
  palette.accent,
]

/** Add (after a style change) or update every overlay source and layer, honouring visibility. */
function drawOverlays(map, data, visible) {
  if (!map.isStyleLoaded()) return
  const palette = readPalette()
  for (const [name, geojson] of Object.entries(data)) {
    const id = `wpw-src-${name}`
    const source = map.getSource(id)
    if (source) source.setData(geojson)
    else map.addSource(id, { type: 'geojson', data: geojson })
  }
  for (const [toggle, layers] of Object.entries(layerSpecs(palette))) {
    for (const layer of layers) {
      if (!map.getLayer(layer.id)) {
        map.addLayer({ ...layer, source: `wpw-src-${layer.source ?? SOURCE_OF[toggle]}` })
      }
      map.setLayoutProperty(layer.id, 'visibility', visible[toggle] ? 'visible' : 'none')
    }
  }
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

function estimateElement(estimate) {
  const element = document.createElement('div')
  element.className = 'wpw-estimate'
  element.setAttribute('role', 'img')
  const where = [estimate.city, estimate.country].filter(Boolean).join(', ') || 'Estimate'
  element.setAttribute('aria-label', `Estimate: ${where}, ±${Math.round(estimate.uncertainty_km)} km`)
  const label = document.createElement('span')
  label.textContent = `${where} ±${Math.round(estimate.uncertainty_km)} km`
  element.appendChild(label)
  return element
}

const initialVisibility = () => Object.fromEntries(LAYERS.map((l) => [l.id, !l.initiallyOff]))

export default function MapView({
  basemap,
  cartoStyleUrl,
  report,
  points,
  bulkItems,
  onBasemapChange,
  onLog,
  onSnapshotReady,
}) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const dataRef = useRef({})
  const [visible, setVisible] = useState(initialVisibility)
  const visibleRef = useRef(visible)
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
    map.on('style.load', () => drawOverlays(map, dataRef.current, visibleRef.current))
    const popup = new Popup({ closeButton: false, closeOnClick: false, className: 'wpw-popup' })
    map.on('mouseenter', 'wpw-probes', (event) => {
      map.getCanvas().style.cursor = 'default'
      popup.setLngLat(event.lngLat).setText(event.features[0].properties.title).addTo(map)
    })
    map.on('mouseleave', 'wpw-probes', () => popup.remove())
    map.on('mouseenter', 'wpw-hops', (event) => {
      popup.setLngLat(event.lngLat).setText(event.features[0].properties.title).addTo(map)
    })
    map.on('mouseleave', 'wpw-hops', () => popup.remove())
    mapRef.current = map
    // A PNG of the map layers: the canvas is read inside the next render, before it is cleared.
    onSnapshotReady?.(
      () =>
        new Promise((resolve) => {
          map.once('render', () => map.getCanvas().toBlob(resolve, 'image/png'))
          map.triggerRepaint()
        }),
    )
    return () => {
      map.remove()
      mapRef.current = null
    }
    // The snapshot callback is registered once, with the map.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Bulk run: one pin per located target, coloured by confidence.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !bulkItems) return undefined
    const located = bulkItems.filter((item) => item.summary?.lat !== undefined)
    const markers = located.map((item) => {
      const s = item.summary
      const element = document.createElement('div')
      element.className = 'wpw-pin'
      element.style.setProperty('--pin', confidenceTone(s.confidence))
      element.title = `${item.query} · ${s.city ?? ''} ${s.country ?? ''} · confidence ${s.confidence ?? '?'}`
      element.setAttribute('role', 'img')
      element.setAttribute('aria-label', element.title)
      return new Marker({ element, anchor: 'bottom' }).setLngLat([s.lon, s.lat]).addTo(map)
    })
    if (located.length) {
      const lons = located.map((i) => i.summary.lon)
      const lats = located.map((i) => i.summary.lat)
      map.fitBounds(
        [
          [Math.min(...lons) - 2, Math.max(-85, Math.min(...lats) - 2)],
          [Math.max(...lons) + 2, Math.min(85, Math.max(...lats) + 2)],
        ],
        { padding: 60, maxZoom: 6, duration: 600 },
      )
    }
    return () => markers.forEach((marker) => marker.remove())
  }, [bulkItems])

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

  // GeoJSON overlays of the current report.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    dataRef.current = {
      region: regionFeatures(report),
      disks: diskFeatures(report),
      accuracy: accuracyCircles(points),
      probes: probeFeatures(report),
      paths: pathFeatures(report),
      hops: hopFeatures(report),
    }
    drawOverlays(map, dataRef.current, visibleRef.current)
  }, [report, points])

  // Layer visibility.
  useEffect(() => {
    visibleRef.current = visible
    const map = mapRef.current
    if (map) drawOverlays(map, dataRef.current, visible)
  }, [visible])

  // HTML markers: passive answers, facilities and the estimate.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return undefined
    const shown = points.filter((p) => (p.kind === 'facility' ? visible.facilities : visible.passive))
    const ordered = [...shown].sort((a, b) => (POINT_KINDS[b.kind]?.order ?? 0) - (POINT_KINDS[a.kind]?.order ?? 0))
    const markers = ordered.map((point) =>
      new Marker({ element: markerElement(point), anchor: 'center' }).setLngLat([point.lon, point.lat]).addTo(map),
    )
    const conv = report?.convergence
    if (conv?.location && visible.convergence) {
      const element = document.createElement('div')
      element.className = 'wpw-convergence'
      element.setAttribute('role', 'img')
      const text = `${conv.paths}/${conv.of} paths · ${conv.hostname ?? conv.address}`
      element.setAttribute('aria-label', `Convergence: ${text}`)
      const label = document.createElement('span')
      label.textContent = text
      element.appendChild(label)
      markers.push(
        new Marker({ element, anchor: 'center' }).setLngLat([conv.location.lon, conv.location.lat]).addTo(map),
      )
    }
    if (report?.estimate && visible.estimate) {
      const { location } = report.estimate
      markers.push(
        new Marker({ element: estimateElement(report.estimate), anchor: 'center' })
          .setLngLat([location.lon, location.lat])
          .addTo(map),
      )
    }
    return () => markers.forEach((marker) => marker.remove())
  }, [points, report, visible])

  // Frame the result when the target or its estimate changes.
  const frameKey = `${report?.target?.address ?? ''}|${report?.estimate ? 'e' : 'p'}|${points.length > 0}`
  useEffect(() => {
    const map = mapRef.current
    const bounds = reportBounds(report, points)
    if (map && bounds) map.fitBounds(bounds, { padding: 70, maxZoom: 9, duration: 800 })
    // Only re-frame on a new target or when an estimate first appears, not on every update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frameKey])

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

  const hasData = Boolean(report)
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
      {hasData ? (
        <fieldset className="absolute left-2.5 top-2.5 grid gap-0.5 border border-line bg-sunken/90 px-2.5 py-1.5 font-mono text-[11px] text-fg-muted max-[860px]:hidden">
          <legend className="sr-only">Layers</legend>
          <span className="text-[10px] uppercase tracking-widest text-fg-subtle">layers</span>
          {LAYERS.map((layer) => (
            <label key={layer.id} className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                id={`layer-${layer.id}`}
                checked={visible[layer.id]}
                onChange={(event) => setVisible((v) => ({ ...v, [layer.id]: event.target.checked }))}
                className="m-0 accent-[var(--color-accent)]"
              />
              {layer.label}
            </label>
          ))}
        </fieldset>
      ) : null}
      {report?.probes?.length ? (
        <div className="absolute bottom-2.5 left-[118px] grid gap-1 border border-line bg-sunken/90 px-2.5 py-1.5 font-mono text-[10px] text-fg-muted max-[860px]:hidden">
          <span>min RTT (ms)</span>
          <span className="flex">
            {[1, 2, 3, 4, 5].map((band) => (
              <i key={band} className="block h-1.5 w-7" style={{ background: `var(--color-rtt${band})` }} />
            ))}
          </span>
          <span className="flex justify-between gap-2 tabular-nums">
            <span>0</span>
            <span>5</span>
            <span>15</span>
            <span>50</span>
            <span>150+</span>
          </span>
        </div>
      ) : null}
      <div className="pointer-events-none absolute bottom-2.5 left-2.5 border border-line bg-sunken/90 px-2 py-1 font-mono text-[10.5px] tabular-nums text-fg-muted">
        {cursor ? `${cursor.lat.toFixed(4)}, ${cursor.lng.toFixed(4)}` : 'lat, lon'}
      </div>
    </div>
  )
}
