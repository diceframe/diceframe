import { describe, expect, it } from 'vitest'
import type { TacticalSceneContentRef, TacticalSceneTokenDocument, TacticalSceneTokenProjection } from '../src/api/tacticalScene'
import {
  dispositionCopyKey,
  isGmTokenDocument,
  ownershipCopyKey,
  projectTokenBars,
  tokenActorStatus,
  tokenDisplayName,
} from '../src/features/tactical-scene/tokenDisplay'

function ref(kind: string, id = 'x'): TacticalSceneContentRef {
  return { source_kind: 'diceframe', source_id: 'g', kind, id, digest: '' }
}

function projection(over: Partial<TacticalSceneTokenProjection> = {}): TacticalSceneTokenProjection {
  return {
    canonical_id: 'tok-1',
    actor_ref: null,
    position: { x: 0, y: 0, elevation: 0, level: 0 },
    size: { width: 1, height: 1 },
    rotation: 0,
    texture: null,
    alpha: 1,
    display: {},
    disposition: 0,
    bars: {},
    locked: false,
    ...over,
  }
}

function gmToken(over: Partial<TacticalSceneTokenDocument> = {}): TacticalSceneTokenDocument {
  return {
    schema_version: 1,
    kind: 'token',
    ...projection(),
    hidden: false,
    ring: {},
    sight: {},
    detection_modes: [],
    light: {},
    delta: {},
    external_ids: {},
    flags: {},
    extensions: {},
    provenance: {},
    ...over,
  }
}

describe('tokenActorStatus', () => {
  it('reports unbound, not-imported, bound and unknown references', () => {
    expect(tokenActorStatus(null)).toBe('unbound')
    expect(tokenActorStatus({ source_kind: 'foundry', source_id: 's', kind: 'actor', id: 'a', digest: '' })).toBe('not_imported')
    expect(tokenActorStatus(ref('player', 'gm'))).toBe('bound')
    expect(tokenActorStatus(ref('enemy', 'goblin-1'))).toBe('bound')
    expect(tokenActorStatus(ref('companion', 'wall'))).toBe('bound')
    expect(tokenActorStatus(ref('something-else'))).toBe('unknown')
  })
})

describe('projectTokenBars', () => {
  it('keeps the opaque attribute path and reports missing values without guessing', () => {
    const bars = projectTokenBars({ bar1: { attribute: 'attributes.hp' }, bar2: {} })
    expect(bars).toHaveLength(2)
    expect(bars[0]).toMatchObject({ key: 'bar1', attribute: 'attributes.hp', value: null, max: null, hasValues: false })
    expect(bars[1]).toMatchObject({ key: 'bar2', attribute: '', hasValues: false })
  })

  it('renders a fill only when value and max are literally present', () => {
    const bars = projectTokenBars({ bar1: { attribute: 'attributes.hp', value: 3, max: 10 } })
    expect(bars[0]).toMatchObject({ value: 3, max: 10, hasValues: true })
    expect(projectTokenBars({ bar1: { value: 5 } })[0]!.hasValues).toBe(false)
    expect(projectTokenBars({ bar1: { value: 5, max: 0 } })[0]!.hasValues).toBe(false)
  })

  it('skips non-object bar entries', () => {
    expect(projectTokenBars({ bar1: 'hp', bar2: null, bar3: { attribute: 'x' } })).toHaveLength(1)
  })
})

describe('tokenDisplayName', () => {
  it('uses the GM residual name and falls back to the canonical id for seats', () => {
    expect(tokenDisplayName(gmToken({ extensions: { name: 'Goblin' } }))).toBe('Goblin')
    expect(tokenDisplayName(gmToken())).toBe('tok-1')
    expect(tokenDisplayName(projection())).toBe('tok-1')
    expect(tokenDisplayName(gmToken({ extensions: { name: 42 } }))).toBe('tok-1')
  })

  it('detects the GM document by the hidden field', () => {
    expect(isGmTokenDocument(gmToken())).toBe(true)
    expect(isGmTokenDocument(projection())).toBe(false)
  })
})

describe('copy key mappings', () => {
  it('maps disposition and ownership levels to copy keys', () => {
    expect(dispositionCopyKey(-2)).toBe('dispositionSecret')
    expect(dispositionCopyKey(-1)).toBe('dispositionHostile')
    expect(dispositionCopyKey(0)).toBe('dispositionNeutral')
    expect(dispositionCopyKey(1)).toBe('dispositionFriendly')
    expect(dispositionCopyKey(9)).toBe('dispositionUnknown')
    expect(ownershipCopyKey(0)).toBe('ownershipNone')
    expect(ownershipCopyKey(1)).toBe('ownershipLimited')
    expect(ownershipCopyKey(2)).toBe('ownershipObserver')
    expect(ownershipCopyKey(3)).toBe('ownershipOwner')
  })
})