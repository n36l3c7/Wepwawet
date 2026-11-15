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
    .filter((e) => e.point && e.kind !== 'negative' && e.source !== 'rtt')
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

/** RTT colour band (1 = near, 5 = far): thresholds in milliseconds. */
export const RTT_BANDS = [5, 15, 50, 150]

export function rttBand(rtt) {
  if (rtt === null || rtt === undefined) return 0
  const index = RTT_BANDS.findIndex((limit) => rtt < limit)
  return index === -1 ? 5 : index + 1
}

/** Probes as GeoJSON points, with their RTT band for styling. */
export function probeFeatures(report) {
  return {
    type: 'FeatureCollection',
    features: (report?.probes ?? []).map((p, index) => ({
      type: 'Feature',
      properties: {
        id: index,
        band: rttBand(p.min_rtt),
        rtt: p.min_rtt,
        title: `${[p.probe.city, p.probe.country].filter(Boolean).join(', ')} · ${
          p.min_rtt === null ? 'no reply' : `${p.min_rtt.toFixed(1)} ms`
        }`,
      },
      geometry: { type: 'Point', coordinates: [p.probe.location.lon, p.probe.location.lat] },
    })),
  }
}

/** The `limit` tightest RTT disks (the ones that shape the region). */
export function diskFeatures(report, limit = 12) {
  const answered = (report?.probes ?? []).filter((p) => p.max_distance_km !== null && p.max_distance_km !== undefined)
  answered.sort((a, b) => a.max_distance_km - b.max_distance_km)
  return {
    type: 'FeatureCollection',
    features: answered.slice(0, limit).map((p) => ({
      type: 'Feature',
      properties: { band: rttBand(p.min_rtt) },
      geometry: circlePolygon(p.probe.location.lon, p.probe.location.lat, Math.min(p.max_distance_km, 19000)),
    })),
  }
}

/** The feasible region polygon and the estimate's uncertainty circle. */
export function regionFeatures(report) {
  const features = []
  if (report?.region)
    features.push({ type: 'Feature', properties: { kind: 'region' }, geometry: report.region.geometry })
  if (report?.estimate) {
    const { location, uncertainty_km: radius } = report.estimate
    features.push({
      type: 'Feature',
      properties: { kind: 'uncertainty' },
      geometry: circlePolygon(location.lon, location.lat, Math.min(radius, 19000)),
    })
  }
  return { type: 'FeatureCollection', features }
}

/** Bounds of the region (or estimate) when there is one, else of the passive points. */
export function reportBounds(report, points) {
  if (report?.estimate) {
    const { location, uncertainty_km: radius } = report.estimate
    const ring = circlePolygon(location.lon, location.lat, Math.max(radius, 20)).coordinates[0]
    const lons = ring.map((c) => c[0])
    const lats = ring.map((c) => c[1])
    return [
      [Math.max(-180, Math.min(...lons)), Math.max(-85, Math.min(...lats))],
      [Math.min(180, Math.max(...lons)), Math.min(85, Math.max(...lats))],
    ]
  }
  return frameBounds(points)
}

SOURCE_LABELS.rtt = 'RTT measurements'

/** Colour tokens of the traceroute paths, cycled. */
export const PATH_COLORS = ['accent', 'accent2', 't3', 't4', 't5']

/**
 * Traceroute paths as lines: probe, then every located hop in order, then the end point
 * (the estimate, else the convergence router). Hops without a location are skipped.
 */
export function pathFeatures(report) {
  const end = report?.estimate?.location ?? report?.convergence?.location ?? null
  return {
    type: 'FeatureCollection',
    features: (report?.traceroutes ?? []).map((trace, index) => {
      const coordinates = [[trace.probe.location.lon, trace.probe.location.lat]]
      for (const hop of trace.hops) {
        if (hop.location) coordinates.push([hop.location.lon, hop.location.lat])
      }
      if (end) coordinates.push([end.lon, end.lat])
      return {
        type: 'Feature',
        properties: { index, color: PATH_COLORS[index % PATH_COLORS.length] },
        geometry: { type: 'LineString', coordinates },
      }
    }),
  }
}

/** Located hops of every path, as points. */
export function hopFeatures(report) {
  const features = []
  ;(report?.traceroutes ?? []).forEach((trace, index) => {
    for (const hop of trace.hops) {
      if (!hop.location) continue
      features.push({
        type: 'Feature',
        properties: {
          color: PATH_COLORS[index % PATH_COLORS.length],
          title: `${hop.hostname ?? hop.address} · ${hop.location_label ?? ''}${
            hop.min_rtt === null ? '' : ` · ${hop.min_rtt.toFixed(1)} ms`
          }`,
        },
        geometry: { type: 'Point', coordinates: [hop.location.lon, hop.location.lat] },
      })
    }
  })
  return { type: 'FeatureCollection', features }
}

SOURCE_LABELS.traceroute = 'Traceroutes'
