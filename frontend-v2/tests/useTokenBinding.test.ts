import { ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import type { TacticalSceneTokenDocument, TacticalSceneWriteResponse } from '../src/api/tacticalScene'
import { diceframeActorRef, useTokenBinding } from '../src/features/tactical-scene/useTokenBinding'

function token(id: string, over: Partial<TacticalSceneTokenDocument> = {}): TacticalSceneTokenDocument {
  return {
    schema_version: 1,
    kind: 'token',
    canonical_id: id,
    actor_ref: { source_kind: 'foundry', source_id: 's', kind: 'actor', id: 'a', digest: '' },
    position: { x: 100, y: 200, elevation: 0, level: 0 },
    size: { width: 1, height: 1 },
    rotation: 0,
    texture: null,
    ring: {},
    alpha: 1,
    display: {},
    disposition: -1,
    hidden: false,
    locked: false,
    sight: {},
    detection_modes: [],
    light: {},
    bars: { bar1: { attribute: 'attributes.hp' } },
    delta: {},
    external_ids: {},
    flags: {},
    extensions: {},
    provenance: {},
    ...over,
  }
}

const CHARACTERS = { players: [{ user_id: 'gm', character_name: 'Hero' }, { user_id: 'p2', character_name: '' }] }
const ACTIONS = {
  ok: true,
  gameplay: {
    encounter_presets: [
      { id: 'preset-1', name: 'Ambush', description: '', difficulty: 'standard', enemies: [{ id: 'goblin-1', name: 'Goblin' }, { id: 'goblin-2', name: 'Goblin 2' }] },
      { id: 'preset-2', name: 'Again', description: '', difficulty: 'standard', enemies: [{ id: 'goblin-1', name: 'Goblin duplicate' }] },
    ],
    combat: {
      status: 'active',
      actors: [
        { actor_id: 'companion:wall', name: 'Wall', kind: 'player', hp: 1, max_hp: 1, position: 0 },
        { actor_id: 'enemy:goblin-1', name: 'Goblin live', kind: 'enemy', hp: 1, max_hp: 1, position: 0 },
        { actor_id: 'player:gm', name: 'Hero live', kind: 'player', hp: 1, max_hp: 1, position: 0 },
      ],
    },
  },
}

function writeResponse(revision: number): TacticalSceneWriteResponse {
  return { ok: true, operation: 'tactical_scene_placeable_update', scene_id: 'scene-a', revision, active_scene_ref: null, scene: null }
}

interface Call { path: string; method: string; body: Record<string, unknown> | null }

function mockApi(writeImpl?: (call: Call) => unknown) {
  const calls: Call[] = []
  const impl = vi.fn(async (path: string, init: RequestInit = {}) => {
    const method = (init.method || 'GET').toUpperCase()
    const body = typeof init.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : null
    const call = { path, method, body }
    calls.push(call)
    if (path.includes('/characters')) return CHARACTERS
    if (path.includes('/available-actions')) return ACTIONS
    if (method === 'PATCH') {
      if (writeImpl) return writeImpl(call)
      return writeResponse(8)
    }
    throw new Error(`unexpected request: ${method} ${path}`)
  })
  vi.spyOn(client, 'api').mockImplementation(impl as typeof client.api)
  return calls
}

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
})

describe('diceframeActorRef', () => {
  it('builds the ContentRef shape the grid authority binds', () => {
    expect(diceframeActorRef('g', 'player', 'gm')).toEqual({ source_kind: 'diceframe', source_id: 'g', kind: 'player', id: 'gm', digest: '' })
  })
})

describe('useTokenBinding target loading', () => {
  it('merges players, preset enemies and live combatants, deduplicated by kind:id', async () => {
    mockApi()
    i18n.global.locale.value = 'en'
    const binding = useTokenBinding(ref('g'))
    await binding.loadTargets()
    expect(binding.loadError.value).toBe('')
    const keys = binding.targets.value.map(target => `${target.kind}:${target.id}`)
    expect(keys).toContain('player:gm')
    expect(keys).toContain('player:p2')
    expect(keys).toContain('enemy:goblin-1')
    expect(keys).toContain('enemy:goblin-2')
    expect(keys).toContain('companion:wall')
    // goblin-1 appears in two presets and combat: only one entry, first label wins.
    expect(keys.filter(key => key === 'enemy:goblin-1')).toHaveLength(1)
    expect(binding.targets.value.find(target => target.id === 'goblin-1')?.label).toBe('Goblin')
    // empty character_name falls back to the uid
    expect(binding.targets.value.find(target => target.id === 'p2')?.label).toBe('p2')
  })

  it('still loads players when the gameplay view is unavailable', async () => {
    vi.spyOn(client, 'api').mockImplementation((async (path: string) => {
      if (path.includes('/characters')) return CHARACTERS
      throw new ApiError('no ruleset', 404, 'not_found')
    }) as typeof client.api)
    const binding = useTokenBinding(ref('g'))
    await binding.loadTargets()
    expect(binding.loadError.value).toBe('')
    expect(binding.targets.value.map(target => target.kind)).toEqual(['player', 'player'])
  })
})

describe('useTokenBinding writes', () => {
  it('binds through placeable_update with the full token document and expected_revision', async () => {
    const calls = mockApi()
    const binding = useTokenBinding(ref('g'))
    const original = token('t1')
    const response = await binding.bind('scene-a', original, 'player', 'gm', 7)
    expect(response.revision).toBe(8)
    const patch = calls.find(call => call.method === 'PATCH')!
    expect(patch.path).toBe('/games/g/tactical-scenes/scene-a/placeables/tokens/t1')
    expect(patch.body!.expected_revision).toBe(7)
    const placeable = patch.body!.placeable as TacticalSceneTokenDocument
    expect(placeable.canonical_id).toBe('t1')
    expect(placeable.actor_ref).toEqual({ source_kind: 'diceframe', source_id: 'g', kind: 'player', id: 'gm', digest: '' })
    // the rest of the document is preserved, not stripped to a partial patch
    expect(placeable.bars).toEqual(original.bars)
    expect(placeable.disposition).toBe(-1)
    expect(binding.conflict.value).toBe(false)
  })

  it('unbinds by setting actor_ref to null', async () => {
    const calls = mockApi()
    const binding = useTokenBinding(ref('g'))
    await binding.unbind('scene-a', token('t1'), 7)
    const placeable = calls.find(call => call.method === 'PATCH')!.body!.placeable as TacticalSceneTokenDocument
    expect(placeable.actor_ref).toBeNull()
  })

  it('flags a stale revision conflict and rethrows', async () => {
    mockApi(() => { throw new ApiError('stale', 409, 'tactical_scene_stale_revision') })
    const binding = useTokenBinding(ref('g'))
    await expect(binding.bind('scene-a', token('t1'), 'enemy', 'goblin-1', 7)).rejects.toBeInstanceOf(ApiError)
    expect(binding.conflict.value).toBe(true)
    expect(binding.saveError.value).toContain('updated elsewhere')
    expect(binding.saving.value).toBe(false)
  })

  it('surfaces a non-conflict write error without setting conflict', async () => {
    mockApi(() => { throw new ApiError('bad', 422, 'tactical_scene_invalid_payload') })
    const binding = useTokenBinding(ref('g'))
    await expect(binding.unbind('scene-a', token('t1'), 7)).rejects.toBeInstanceOf(ApiError)
    expect(binding.conflict.value).toBe(false)
    expect(binding.saveError.value).toContain('invalid')
  })
})