/**
 * The source label that rides along with everything on screen.
 *
 * The rule from the brief: anything shown to the user is labelled live,
 * cached, preset, generated, inferred or mock where it applies, and a mock
 * result must never look live. This component is the one place that styling
 * is decided, so a lane cannot quietly render a mock as though it were real.
 */

export type SourceLabel =
  | 'live'
  | 'cached'
  | 'preset'
  | 'generated'
  | 'inferred'
  | 'mock'

const COPY: Record<SourceLabel, string> = {
  live: 'Read from the source just now',
  cached: 'Reused from an earlier read',
  preset: 'A prepared stand-in, not this room',
  generated: 'Produced by the app or a model',
  inferred: 'Read out of listing text, not a documented field',
  mock: 'Fake data, for building and testing',
}

export function SourceBadge({
  label,
  at,
}: {
  label: SourceLabel
  /** When the value was read. Shown for cached, which is meaningless without it. */
  at?: string
}) {
  const suffix = label === 'cached' && at ? ` · ${at}` : ''
  return (
    <span className={`badge badge-${label}`} title={COPY[label]}>
      {label}
      {suffix}
    </span>
  )
}
