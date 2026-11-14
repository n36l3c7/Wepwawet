import { test } from 'node:test'
import assert from 'node:assert/strict'
import { JACKAL_PRESETS, jackalFrame, jackalText, microJackal, renderJackal } from './asciiJackal.js'

const text = (options) => jackalText({ ...JACKAL_PRESETS.medium, ...options })

test('every preset fills its grid exactly', () => {
  for (const [name, preset] of Object.entries(JACKAL_PRESETS)) {
    const rows = renderJackal(preset)
    assert.equal(rows.length, preset.height, name)
    for (const row of rows) assert.equal(row.map((s) => s.text).join('').length, preset.width, name)
  }
})

test('the eye sits on one row and shows the pupil', () => {
  const eyeRows = renderJackal(JACKAL_PRESETS.large).filter((row) => row.some((s) => s.part === 'eye'))
  assert.equal(eyeRows.length, 1)
  assert.match(eyeRows[0].find((s) => s.part === 'eye').text, /O/)
})

test('moods change only the eye', () => {
  assert.match(text({ eyes: 'x' }), /x/)
  assert.match(text({ eyes: 'found' }), /\^/)
  assert.doesNotMatch(text({ eyes: 'closed' }), /O/)
})

test('gaze moves the pupil', () => {
  const column = (gaze) =>
    text({ gaze })
      .split('\n')
      .find((line) => line.includes('O'))
      .indexOf('O')
  assert.ok(column(1) > column(0))
  assert.ok(column(-1) < column(0))
})

test('sniff swaps the nose glyph', () => {
  const nose = (sniff) =>
    renderJackal({ ...JACKAL_PRESETS.medium, sniff })
      .flat()
      .filter((s) => s.part === 'nose')
      .map((s) => s.text)
      .join('')
  assert.notEqual(nose(true), nose(false))
})

test('the collar is drawn', () => {
  const collar = renderJackal(JACKAL_PRESETS.medium)
    .flat()
    .filter((s) => s.part === 'collar')
  assert.ok(collar.length > 0)
})

test('animation frames are deterministic and blink sometimes', () => {
  assert.deepEqual(jackalFrame(5), jackalFrame(5))
  const frames = Array.from({ length: 30 }, (_, t) => jackalFrame(t))
  assert.ok(frames.some((f) => f.eyes === 'closed'))
  assert.ok(frames.some((f) => f.gaze !== 0))
})

test('micro faces', () => {
  assert.equal(microJackal('x'), '/\\x>')
  assert.equal(microJackal('closed'), '/\\->')
  assert.notEqual(microJackal('open', 0), microJackal('open', 1))
})
