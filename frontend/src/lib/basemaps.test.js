import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { offlineStyle, themeCartoStyle } from './basemaps.js'

const require = createRequire(import.meta.url)
const topology = JSON.parse(readFileSync(require.resolve('world-atlas/countries-110m.json'), 'utf-8'))

const palette = {
  ocean: '#000001',
  land: '#000002',
  landHi: '#000003',
  border: '#000004',
  line: '#000005',
  lineSoft: '#000006',
  sunken: '#000007',
  fgMuted: '#000008',
  fgSubtle: '#000009',
  accent: '#00000a',
}

test('offline style draws land, borders and coast from the bundled topology', () => {
  const style = offlineStyle(topology, palette)
  assert.equal(style.version, 8)
  assert.equal(style.sources.countries.data.type, 'FeatureCollection')
  assert.ok(style.sources.countries.data.features.length > 150)
  assert.equal(style.sources.borders.data.type, 'MultiLineString')
  assert.deepEqual(
    style.layers.map((l) => l.id),
    ['background', 'land', 'borders', 'coast'],
  )
  assert.equal(style.layers[0].paint['background-color'], palette.ocean)
  assert.equal(style.layers[1].paint['fill-color'], palette.land)
  assert.ok(!('glyphs' in style), 'the offline map must not need a glyph server')
})

const carto = {
  version: 8,
  sources: { carto: { type: 'vector', url: 'https://example.invalid' } },
  layers: [
    { id: 'background', type: 'background', paint: { 'background-color': '#fff' } },
    { id: 'water', type: 'fill', source: 'carto', 'source-layer': 'water', paint: { 'fill-color': '#fff' } },
    { id: 'river', type: 'line', source: 'carto', 'source-layer': 'waterway', paint: { 'line-color': '#fff' } },
    {
      id: 'park',
      type: 'fill',
      source: 'carto',
      'source-layer': 'park',
      paint: { 'fill-color': '#fff', 'fill-outline-color': '#fff' },
    },
    { id: 'border', type: 'line', source: 'carto', 'source-layer': 'boundary', paint: { 'line-color': '#fff' } },
    { id: 'road', type: 'line', source: 'carto', 'source-layer': 'transportation', paint: { 'line-color': '#fff' } },
    { id: 'city', type: 'symbol', source: 'carto', 'source-layer': 'place', layout: {}, paint: {} },
    { id: 'poi', type: 'symbol', source: 'carto', 'source-layer': 'poi', layout: {} },
  ],
}

test('CARTO layers are recoloured by source layer', () => {
  const themed = themeCartoStyle(carto, palette)
  const paint = Object.fromEntries(themed.layers.map((l) => [l.id, l.paint]))
  assert.equal(paint.background['background-color'], palette.land)
  assert.equal(paint.water['fill-color'], palette.ocean)
  assert.equal(paint.river['line-color'], palette.ocean)
  assert.equal(paint.park['fill-color'], palette.landHi)
  assert.ok(!('fill-outline-color' in paint.park))
  assert.equal(paint.border['line-color'], palette.border)
  assert.equal(paint.road['line-color'], palette.lineSoft)
  assert.equal(paint.city['text-color'], palette.fgMuted)
  assert.equal(paint.poi['text-color'], palette.fgSubtle)
  assert.equal(paint.poi['text-halo-color'], palette.sunken)
})

test("CARTO's own source-layer names are recognised too", () => {
  const style = {
    version: 8,
    layers: [
      { id: 'b', type: 'line', 'source-layer': 'boundaries', paint: { 'line-color': '#fff' } },
      { id: 'r', type: 'line', 'source-layer': 'roads', paint: { 'line-color': '#fff' } },
      { id: 'h', type: 'fill', 'source-layer': 'buildings', paint: { 'fill-color': '#fff' } },
      { id: 'p', type: 'symbol', 'source-layer': 'places', paint: {} },
    ],
  }
  const [b, r, h, p] = themeCartoStyle(style, palette).layers.map((l) => l.paint)
  assert.equal(b['line-color'], palette.border)
  assert.equal(r['line-color'], palette.lineSoft)
  assert.equal(h['fill-color'], palette.landHi)
  assert.equal(p['text-color'], palette.fgMuted)
})

test('theming does not modify the input style', () => {
  const before = JSON.stringify(carto)
  themeCartoStyle(carto, palette)
  assert.equal(JSON.stringify(carto), before)
})
