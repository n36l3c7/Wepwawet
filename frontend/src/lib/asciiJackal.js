/*
 * ============================================================================
 * ASCII JACKAL — pure generator behind the loading / empty / error states
 * ============================================================================
 *
 * Draws Wepwawet's head in profile (facing right) on a `width` × `height` grid of
 * monospace cells, from simple shapes: cranium, tapered snout, two tall ears,
 * neck with an Egyptian broad collar. Fur is shaded with a character `ramp`
 * (light from the top-left). Monospace cells are about 0.6 as wide as they are
 * tall, so shapes are laid out in physical units and sampled per cell.
 *
 * `renderJackal()` returns rows of segments so the UI can colour the parts:
 *   [ [ { text: '  .:-=', part: null }, { text: 'o', part: 'eye' }, … ], … ]
 * part: null (fur), 'eye', 'nose', 'collar'.
 *
 * Eyes: 'open' (pupil, shifted by `gaze` -1 … 1), 'closed', 'x' (error),
 * 'found' (a happy arc). `sniff` swaps the nose glyph, `twitch` bends the ear.
 */

export const JACKAL_RAMPS = {
  ascii: ' .:-=+*#%@',
  short: ' .:-=+*#',
  block: ' ░░▒▒▓▓██',
}

export const JACKAL_PRESETS = {
  large: { width: 60, height: 26, ramp: JACKAL_RAMPS.ascii },
  medium: { width: 44, height: 19, ramp: JACKAL_RAMPS.ascii },
  small: { width: 30, height: 13, ramp: JACKAL_RAMPS.short },
}

const CELL_ASPECT = 0.6 // width / height of one monospace cell

/** Point-in-polygon (even-odd rule). */
function inside(poly, u, v) {
  let hit = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [ui, vi] = poly[i]
    const [uj, vj] = poly[j]
    if (vi > v !== vj > v && u < ((uj - ui) * (v - vi)) / (vj - vi) + ui) hit = !hit
  }
  return hit
}

const ellipse = (u, v, cu, cv, ru, rv) => ((u - cu) / ru) ** 2 + ((v - cv) / rv) ** 2

/** The silhouette in physical units: x in [0, 1.4], y in [0, 1]. */
function shapes(twitch) {
  const tip = twitch ? 0.05 : 0
  return {
    backEar: [
      [0.3, 0.44],
      [0.37 + tip / 2, 0.02],
      [0.53, 0.38],
    ],
    backEarInner: [
      [0.36, 0.38],
      [0.385 + tip / 2, 0.12],
      [0.47, 0.36],
    ],
    frontEar: [
      [0.47, 0.4],
      [0.6 + tip, 0.0],
      [0.69, 0.44],
    ],
    frontEarInner: [
      [0.53, 0.38],
      [0.6 + tip, 0.1],
      [0.645, 0.4],
    ],
    snout: [
      [0.6, 0.41],
      [0.9, 0.49],
      [1.12, 0.56],
      [1.25, 0.6],
      [1.29, 0.645],
      [1.25, 0.69],
      [1.04, 0.72],
      [0.8, 0.77],
      [0.62, 0.79],
    ],
    neck: [
      [0.3, 0.62],
      [0.6, 0.74],
      [0.72, 1.0],
      [0.22, 1.0],
    ],
  }
}

/**
 * @param {object} [options]
 * @param {number} [options.width=44]
 * @param {number} [options.height=19]
 * @param {string} [options.ramp]
 * @param {'open'|'closed'|'x'|'found'} [options.eyes='open']
 * @param {number} [options.gaze=0]   -1 … 1
 * @param {boolean} [options.sniff=false]
 * @param {boolean} [options.twitch=false]
 * @returns {Array<Array<{text: string, part: null|'eye'|'nose'|'collar'}>>}
 */
export function renderJackal({
  width = 44,
  height = 19,
  ramp = JACKAL_RAMPS.ascii,
  eyes = 'open',
  gaze = 0,
  sniff = false,
  twitch = false,
} = {}) {
  const span = (width * CELL_ASPECT) / height // physical width of the grid when height is 1
  const scale = Math.min(span / 1.4, 1) // shrink to fit narrow grids
  const offset = (span - 1.4 * scale) / 2
  const s = shapes(twitch)
  const eye = { u: 0.74, v: 0.5 }
  const g = Math.max(-1, Math.min(1, gaze))
  const cellU = span / width
  const cellV = 1 / height
  const eyeRow = Math.floor(eye.v * height)

  const rows = []
  for (let r = 0; r < height; r++) {
    const segments = []
    const push = (text, part) => {
      const last = segments[segments.length - 1]
      if (last && last.part === part) last.text += text
      else segments.push({ text, part })
    }
    for (let c = 0; c < width; c++) {
      const u = ((c + 0.5) * cellU - offset) / scale
      const v = (r + 0.5) * cellV
      const cranium = ellipse(u, v, 0.48, 0.55, 0.23, 0.19) <= 1
      const body =
        cranium || inside(s.snout, u, v) || inside(s.neck, u, v) || inside(s.backEar, u, v) || inside(s.frontEar, u, v)
      if (!body) {
        push(' ', null)
        continue
      }
      // Eye: an almond around (eye.u, eye.v), roughly 3 × 1 cells.
      const du = (u - eye.u) / (cellU / scale)
      if (r === eyeRow && Math.abs(du) <= 2.5) {
        push(...eyeCell(eyes, Math.round(du - g)), 'eye')
        continue
      }
      // Nose: the snout tip.
      if (ellipse(u, v, 1.265, 0.645, 0.035, 0.04) <= 1) {
        push(sniff ? '@' : 'o', 'nose')
        continue
      }
      // Mouth line along the lower jaw.
      const mouthV = 0.7 + (1.2 - u) * 0.06
      if (u > 0.9 && u < 1.22 && Math.abs(v - mouthV) < cellV / 2 / scale) {
        push('_', null)
        continue
      }
      // Broad collar (usekh): turquoise bands on the neck.
      if (v > 0.84 && inside(s.neck, u, v) && !cranium) {
        const band = Math.floor((v - 0.84) / (cellV / scale))
        push(band % 2 === 0 ? '=' : '"', 'collar')
        continue
      }
      // Inner ears are hollow: lightest shade.
      if (inside(s.backEarInner, u, v) || inside(s.frontEarInner, u, v)) {
        push(ramp[1], null)
        continue
      }
      // Light from the top-left: denser glyphs (brighter on a dark screen) up and to the left.
      const level = Math.max(0, Math.min(0.999, 0.12 + 0.62 * (1 - v) + 0.22 * (1 - u / 1.4)))
      push(ramp[1 + Math.floor(level * (ramp.length - 1))], null)
    }
    rows.push(segments)
  }
  return rows
}

/** One eye cell from its column offset `dc` (-2 … 2): returns [character]. */
function eyeCell(eyes, dc) {
  if (eyes === 'closed') return [dc >= -2 && dc <= 2 ? '-' : ' ']
  if (eyes === 'x') return [dc === 0 ? 'x' : dc === -1 || dc === 1 ? ' ' : '-']
  if (eyes === 'found') return [dc === 0 ? '^' : dc === -1 || dc === 1 ? ' ' : '-']
  return [dc === 0 ? 'O' : dc === -1 ? '(' : dc === 1 ? ')' : '-']
}

/** Plain text of a rendered jackal (tests, CLI banner). */
export function jackalText(options) {
  return renderJackal(options)
    .map((row) => row.map((s) => s.text).join(''))
    .join('\n')
}

/**
 * Animation frame for tick `t` (one tick every 400 ms): looks around, blinks,
 * sniffs, twitches an ear.
 */
export function jackalFrame(t) {
  const gaze = [0, 0, 1, 1, 0, 0, -1, -1][Math.floor(t / 2) % 8]
  const blink = t % 11 === 7
  return { eyes: blink ? 'closed' : 'open', gaze, sniff: t % 2 === 0, twitch: t % 9 === 4 }
}

/** Inline face for buttons and the title bar, e.g. "/\o>". */
export function microJackal(mood = 'open', t = 0) {
  if (mood === 'x') return '/\\x>'
  if (mood === 'closed') return '/\\->'
  if (mood === 'found') return '/\\^>'
  return t % 2 ? '/\\o>' : '/\\o»'
}
