import { useMemo } from 'react'
import { JACKAL_PRESETS, jackalFrame, microJackal, renderJackal } from '../../lib/asciiJackal.js'
import { useTick } from '../../lib/useTick.js'

/*
 * ============================================================================
 * ASCII JACKAL — loading / empty / error illustrations
 * ============================================================================
 *
 *   <AsciiJackal />                               animated: looks around, sniffs
 *   <AsciiJackal mood="closed" tone="muted" />    empty / asleep
 *   <AsciiJackal mood="x" tone="muted" />         error
 *   <AsciiJackal mood="found" />                  result
 *   <MicroJackal />  ·  <MicroJackal mood="x" />  inline "/\o>" for buttons
 *
 * Purely decorative: always aria-hidden, so pair it with a text status. With
 * "reduce motion" enabled in the OS the jackal stays still.
 */

const TONES = { accent: 'text-accent', muted: 'text-fg-subtle' }
const PART_CLASS = {
  eye: 'text-accent2 font-bold',
  nose: 'text-accent2',
  collar: 'text-accent2',
}
const FONT_SIZES = { large: 'text-[9px]', medium: 'text-[10px]', small: 'text-[10px]' }

export default function AsciiJackal({ preset = 'medium', mood = 'animated', tone = 'accent', className = '' }) {
  const animated = mood === 'animated'
  const tick = useTick(animated)
  const frame = animated ? jackalFrame(tick) : { eyes: mood, gaze: 0, sniff: false, twitch: false }
  const { eyes, gaze, sniff, twitch } = frame
  const rows = useMemo(
    () => renderJackal({ ...(JACKAL_PRESETS[preset] ?? JACKAL_PRESETS.medium), eyes, gaze, sniff, twitch }),
    [preset, eyes, gaze, sniff, twitch],
  )
  const eyeClass = mood === 'x' ? 'text-high font-bold' : PART_CLASS.eye

  return (
    <div
      aria-hidden="true"
      className={[
        'select-none whitespace-pre font-mono leading-none',
        FONT_SIZES[preset] ?? FONT_SIZES.medium,
        TONES[tone] ?? TONES.accent,
        className,
      ].join(' ')}
    >
      {rows.map((segments, r) => (
        <div key={r}>
          {segments.map((s, i) =>
            s.part ? (
              <span key={i} className={s.part === 'eye' ? eyeClass : PART_CLASS[s.part]}>
                {s.text}
              </span>
            ) : (
              <span key={i}>{s.text}</span>
            ),
          )}
        </div>
      ))}
    </div>
  )
}

export function MicroJackal({ mood = 'animated', className = '' }) {
  const tick = useTick(mood === 'animated')
  return (
    <span aria-hidden="true" className={`select-none font-mono ${className}`}>
      {microJackal(mood === 'animated' ? 'open' : mood, tick)}
    </span>
  )
}
