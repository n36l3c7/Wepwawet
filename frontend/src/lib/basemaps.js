/*
 * Base maps for MapLibre.
 *
 *  - carto:   CARTO "Dark Matter" vector tiles (OpenStreetMap data), recoloured
 *             with the palette by `themeCartoStyle()`. Needs the network.
 *  - offline: Natural Earth country outlines bundled with the app (world-atlas,
 *             1:50m). No network, no labels.
 */
import { feature, mesh } from 'topojson-client'

export const BASEMAPS = {
  carto: { label: 'CARTO dark', needsNetwork: true },
  offline: { label: 'Vector · offline', needsNetwork: false },
}

/** Palette colours read from the CSS variables defined in index.css. */
export function readPalette(element = document.documentElement) {
  const css = getComputedStyle(element)
  const v = (name) => css.getPropertyValue(`--color-${name}`).trim()
  return {
    ocean: v('ocean'),
    land: v('land'),
    landHi: v('land-hi'),
    border: v('border'),
    line: v('line'),
    lineSoft: v('line-soft'),
    sunken: v('sunken'),
    fgMuted: v('fg-muted'),
    fgSubtle: v('fg-subtle'),
    accent: v('accent'),
  }
}

/** MapLibre style for the bundled Natural Earth outlines. */
export function offlineStyle(topology, palette) {
  const countries = feature(topology, topology.objects.countries)
  const borders = mesh(topology, topology.objects.countries, (a, b) => a !== b)
  const coast = mesh(topology, topology.objects.countries, (a, b) => a === b)
  return {
    version: 8,
    name: 'wepwawet-offline',
    sources: {
      countries: { type: 'geojson', data: countries, attribution: 'Natural Earth' },
      borders: { type: 'geojson', data: borders },
      coast: { type: 'geojson', data: coast },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': palette.ocean } },
      { id: 'land', type: 'fill', source: 'countries', paint: { 'fill-color': palette.land } },
      {
        id: 'borders',
        type: 'line',
        source: 'borders',
        paint: { 'line-color': palette.border, 'line-width': 0.8, 'line-dasharray': [3, 2] },
      },
      { id: 'coast', type: 'line', source: 'coast', paint: { 'line-color': palette.border, 'line-width': 0.9 } },
    ],
  }
}

// Source layers by role, in CARTO's schema and in OpenMapTiles' (which CARTO may adopt).
const WATER = new Set(['water', 'waterway'])
const LAND_DETAIL = new Set(['landcover', 'landuse', 'park', 'building', 'buildings', 'aeroway'])
const BOUNDARIES = new Set(['boundary', 'boundaries'])
const ROADS = new Set(['transportation', 'roads'])
const PLACES = new Set(['place', 'places'])

/**
 * Recolour a CARTO / OpenMapTiles style with the palette. Layers are matched by
 * their `source-layer`, so the function survives CARTO renaming layer ids.
 * Returns a new style object; the input is not modified.
 */
export function themeCartoStyle(style, palette) {
  const layers = style.layers.map((layer) => {
    const paint = { ...(layer.paint ?? {}) }
    const sourceLayer = layer['source-layer'] ?? ''
    if (layer.type === 'background') {
      paint['background-color'] = palette.land
    } else if (WATER.has(sourceLayer)) {
      if (layer.type === 'fill') paint['fill-color'] = palette.ocean
      if (layer.type === 'line') paint['line-color'] = palette.ocean
    } else if (LAND_DETAIL.has(sourceLayer)) {
      if (layer.type === 'fill') {
        paint['fill-color'] = palette.landHi
        delete paint['fill-outline-color']
      }
      if (layer.type === 'line') paint['line-color'] = palette.landHi
    } else if (BOUNDARIES.has(sourceLayer)) {
      if (layer.type === 'line') paint['line-color'] = palette.border
    } else if (ROADS.has(sourceLayer)) {
      if (layer.type === 'line') paint['line-color'] = palette.lineSoft
    }
    if (layer.type === 'symbol') {
      const important = PLACES.has(sourceLayer)
      paint['text-color'] = important ? palette.fgMuted : palette.fgSubtle
      paint['text-halo-color'] = palette.sunken
      paint['text-halo-width'] = 1
    }
    return { ...layer, paint }
  })
  return { ...style, layers }
}
