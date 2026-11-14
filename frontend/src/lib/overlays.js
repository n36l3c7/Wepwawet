/*
 * Turn a target report into map overlays: labelled points and accuracy circles.
 * Pure functions, tested with node --test.
 */

const EARTH_RADIUS_KM = 6371.0088

/** Point kinds, drawn with different markers. */
export const POINT_KINDS = {
  geoip: { label: 'GeoIP answer', order: 2 },
  located: { label: 'Located by a service', order: 1 },
  facility: { label: 'PeeringDB facility', order: 3 },
}

function kindOf(evidence) {
  if (evidence.source === 'peeringdb') return 'facility'
  if (evidence.source === 'geoip') return 'geoip'
  return 'located'
}

/** Labelled points for every evidence item that has coordinates. */
export function reportPoints(report) {
  if (!report) return []
  return report.evidence
    .filter((e) => e.point)
    .map((e, index) => ({
      id: `${e.source}-${index}`,
      kind: kindOf(e),
      lon: e.point.lon,
      lat: e.point.lat,
      label: e.label || e.source,
      title: e.summary,
      radiusKm: e.radius_km ?? null,
    }))
}

/** A geodesic circle as a GeoJSON polygon ring (lon, lat), `steps` vertices. */
export function circlePolygon(lon, lat, radiusKm, steps = 64) {
  const d = radiusKm / EARTH_RADIUS_KM
  const φ1 = (lat * Math.PI) / 180
  const λ1 = (lon * Math.PI) / 180
  const ring = []
  for (let i = 0; i <= steps; i++) {
    const θ = (2 * Math.PI * i) / steps
    const φ2 = Math.asin(Math.sin(φ1) * Math.cos(d) + Math.cos(φ1) * Math.sin(d) * Math.cos(θ))
    const λ2 = λ1 + Math.atan2(Math.sin(θ) * Math.sin(d) * Math.cos(φ1), Math.cos(d) - Math.sin(φ1) * Math.sin(φ2))
    ring.push([(((λ2 * 180) / Math.PI + 540) % 360) - 180, (φ2 * 180) / Math.PI])
  }
  return { type: 'Polygon', coordinates: [ring] }
}

/** Accuracy circles of GeoIP-like answers, as a GeoJSON FeatureCollection. */
export function accuracyCircles(points) {
  return {
    type: 'FeatureCollection',
    features: points
      .filter((p) => p.radiusKm && p.kind !== 'facility')
      .map((p) => ({
        type: 'Feature',
        properties: { id: p.id, kind: p.kind },
        geometry: circlePolygon(p.lon, p.lat, Math.min(p.radiusKm, 2000)),
      })),
  }
}

/**
 * Bounds to frame: the located answers first (facilities can span the planet),
 * falling back to every point. Returns [[west, south], [east, north]] or null.
 */
export function frameBounds(points) {
  const preferred = points.filter((p) => p.kind !== 'facility')
  const chosen = preferred.length ? preferred : points
  if (!chosen.length) return null
  const lons = chosen.map((p) => p.lon)
  const lats = chosen.map((p) => p.lat)
  const pad = chosen.length === 1 ? 1.5 : 0.5
  return [
    [Math.max(-180, Math.min(...lons) - pad), Math.max(-85, Math.min(...lats) - pad)],
    [Math.min(180, Math.max(...lons) + pad), Math.min(85, Math.max(...lats) + pad)],
  ]
}

/** Evidence grouped by source, in the order sources first appear. */
export function groupEvidence(report) {
  const groups = new Map()
  for (const item of report?.evidence ?? []) {
    if (!groups.has(item.source)) groups.set(item.source, [])
    groups.get(item.source).push(item)
  }
  return [...groups.entries()].map(([source, items]) => ({ source, items }))
}

/** Human labels of the sources. */
export const SOURCE_LABELS = {
  geoip: 'GeoIP databases',
  ipmap: 'RIPE IPmap',
  ripestat: 'RIPEstat',
  rdap: 'RDAP registration',
  rdns: 'Reverse DNS',
  internetdb: 'Shodan InternetDB',
  shodan: 'Shodan',
  censys: 'Censys',
  peeringdb: 'PeeringDB',
  classify: 'Network classification',
}
