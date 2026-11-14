import { test } from 'node:test'
import assert from 'node:assert/strict'
import { accuracyCircles, circlePolygon, frameBounds, groupEvidence, reportPoints } from './overlays.js'

const report = {
  evidence: [
    { source: 'ripestat', kind: 'info', summary: 'Prefix', point: null },
    {
      source: 'geoip',
      kind: 'soft',
      summary: 'DB-IP: Milan',
      label: 'DB-IP Lite',
      point: { lat: 45.46, lon: 9.19 },
      radius_km: 50,
    },
    { source: 'ipmap', kind: 'soft', summary: 'IPmap: Milan', label: 'RIPE IPmap', point: { lat: 45.47, lon: 9.18 } },
    { source: 'peeringdb', kind: 'soft', summary: 'MIL1', label: 'MIL1', point: { lat: 45.4, lon: 9.1 } },
    { source: 'peeringdb', kind: 'soft', summary: 'SIN1', label: 'SIN1', point: { lat: 1.35, lon: 103.8 } },
  ],
}

test('points carry their kind and label', () => {
  const points = reportPoints(report)
  assert.deepEqual(
    points.map((p) => [p.kind, p.label]),
    [
      ['geoip', 'DB-IP Lite'],
      ['located', 'RIPE IPmap'],
      ['facility', 'MIL1'],
      ['facility', 'SIN1'],
    ],
  )
  assert.deepEqual(reportPoints(null), [])
})

test('a circle closes and keeps its radius', () => {
  const ring = circlePolygon(9.19, 45.46, 50).coordinates[0]
  assert.equal(ring.length, 65)
  assert.deepEqual(
    ring[0],
    ring[64].map((v, i) => (Math.abs(v - ring[0][i]) < 1e-9 ? ring[0][i] : v)),
  )
  const north = ring[0]
  assert.ok(Math.abs(north[1] - 45.46 - 50 / 111.2) < 0.01)
})

test('only answers with an accuracy get a circle', () => {
  const circles = accuracyCircles(reportPoints(report))
  assert.equal(circles.features.length, 1)
  assert.equal(circles.features[0].properties.kind, 'geoip')
})

test('framing ignores far facilities when answers exist', () => {
  const [[west, south], [east, north]] = frameBounds(reportPoints(report))
  assert.ok(west > 8 && east < 10 && south > 44 && north < 46)
  const facilitiesOnly = reportPoints({ evidence: report.evidence.slice(3) })
  assert.ok(frameBounds(facilitiesOnly)[1][0] > 100)
  assert.equal(frameBounds([]), null)
})

test('evidence groups keep first-seen order', () => {
  assert.deepEqual(
    groupEvidence(report).map((g) => [g.source, g.items.length]),
    [
      ['ripestat', 1],
      ['geoip', 1],
      ['ipmap', 1],
      ['peeringdb', 2],
    ],
  )
})
