import type {
  TacticalSceneContentRef,
  TacticalSceneTokenDocument,
  TacticalSceneTokenProjection,
} from '@/api/tacticalScene'

// Display-only helpers for the token detail panel (frontend-05). They read the
// server projection as-is and never resolve mechanics: an unimported Foundry
// actor is reported, never guessed (workplan §5.1).

export type TokenActorStatus = 'unbound' | 'not_imported' | 'bound' | 'unknown'

// The grid combat authority (_token_actor_id in ruleset_gameplay.py) only binds
// kind player/companion/enemy. A FVTT import carries kind="actor" pointing at an
// actor that M1 does not import, so it reads as "not imported", not an error.
export function tokenActorStatus(ref: TacticalSceneContentRef | null): TokenActorStatus {
  if (!ref) return 'unbound'
  if (ref.kind === 'actor') return 'not_imported'
  if (ref.kind === 'player' || ref.kind === 'enemy' || ref.kind === 'companion') return 'bound'
  return 'unknown'
}

export function isGmTokenDocument(
  token: TacticalSceneTokenProjection | TacticalSceneTokenDocument,
): token is TacticalSceneTokenDocument {
  return 'hidden' in token
}

// The FVTT adapter keeps the token's own name as residual extensions.name
// (GM-only). Seat projections carry no name, so the canonical id stands in.
export function tokenDisplayName(
  token: TacticalSceneTokenProjection | TacticalSceneTokenDocument,
): string {
  if (isGmTokenDocument(token)) {
    const name = token.extensions?.name
    if (typeof name === 'string' && name.trim()) return name
  }
  return token.canonical_id
}

export interface TokenBarProjection {
  key: string
  attribute: string
  value: number | null
  max: number | null
  hasValues: boolean
}

// bars is an opaque dict (bar1/bar2 keep their FVTT config). Show the attribute
// path and any value/max literally present on the token; never resolve the
// attribute against an actor, which M1 has not imported.
export function projectTokenBars(bars: Record<string, unknown>): TokenBarProjection[] {
  const result: TokenBarProjection[] = []
  for (const [key, raw] of Object.entries(bars)) {
    if (!raw || typeof raw !== 'object') continue
    const bar = raw as Record<string, unknown>
    const attribute = typeof bar.attribute === 'string' ? bar.attribute : ''
    const value = typeof bar.value === 'number' && Number.isFinite(bar.value) ? bar.value : null
    const max = typeof bar.max === 'number' && Number.isFinite(bar.max) ? bar.max : null
    result.push({ key, attribute, value, max, hasValues: value !== null && max !== null && max > 0 })
  }
  return result
}

export function dispositionCopyKey(disposition: number): string {
  if (disposition === -2) return 'dispositionSecret'
  if (disposition === -1) return 'dispositionHostile'
  if (disposition === 0) return 'dispositionNeutral'
  if (disposition === 1) return 'dispositionFriendly'
  return 'dispositionUnknown'
}

// Ownership levels per workplan §6.2: 0 none, 1 limited, 2 observer, 3 owner.
export function ownershipCopyKey(level: number): string {
  if (level === 1) return 'ownershipLimited'
  if (level === 2) return 'ownershipObserver'
  if (level === 3) return 'ownershipOwner'
  return 'ownershipNone'
}