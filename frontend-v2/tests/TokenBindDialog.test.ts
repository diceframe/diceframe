import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import TokenBindDialog from '../src/features/tactical-scene/TokenBindDialog.vue'
import type { TacticalSceneDocument, TacticalSceneTokenDocument } from '../src/api/tacticalScene'

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
    bars: {},
    delta: {},
    external_ids: {},
    flags: {},
    extensions: {},
    provenance: {},
    ...over,
  }
}

function gmScene(t: TacticalSceneTokenDocument, revision: number): TacticalSceneDocument {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: 'scene-a',
    display_name: 'Chapel',
    navigation_name: '',
    source_ref: null,
    external_ids: {},
    ownership: {},
    sort: 0,
    background: null,
    foreground: null,
    thumbnail: null,
    dimensions: { width: 1200, height: 900, padding: 0, offset_x: 0, offset_y: 0, initial_view: { x: 0, y: 0, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: '', color: '', alpha: 0.2, distance: 5 },
    environment: { darkness: 0, global_illumination: false, fog: { exploration: false, reset_policy: '' }, weather: '' },
    placeables: { tokens: { [t.canonical_id]: t }, tiles: {}, walls: {}, lights: {}, sounds: {}, notes: {}, drawings: {}, templates: {}, regions: {} },
    levels: [],
    flags: {},
    extensions: {},
    provenance: {},
    revision,
  }
}

const CHARACTERS = { players: [{ user_id: 'gm', character_name: 'Hero' }] }
const ACTIONS = {
  ok: true,
  gameplay: {
    encounter_presets: [{ id: 'p1', name: 'Ambush', description: '', difficulty: 'standard', enemies: [{ id: 'goblin-1', name: 'Goblin' }] }],
    combat: { status: 'none', actors: [] },
  },
}

function mockApi(options: { stale?: boolean; freshToken?: TacticalSceneTokenDocument; freshRevision?: number } = {}) {
  const calls: { path: string; method: string; body: Record<string, unknown> | null }[] = []
  vi.spyOn(client, 'api').mockImplementation((async (path: string, init: RequestInit = {}) => {
    const method = (init.method || 'GET').toUpperCase()
    const body = typeof init.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : null
    calls.push({ path, method, body })
    if (path.includes('/characters')) return CHARACTERS
    if (path.includes('/available-actions')) return ACTIONS
    if (method === 'PATCH') {
      if (options.stale) throw new ApiError('stale', 409, 'tactical_scene_stale_revision')
      const placeable = body!.placeable as TacticalSceneTokenDocument
      return { ok: true, operation: 'tactical_scene_placeable_update', scene_id: 'scene-a', revision: 8, active_scene_ref: null, scene: gmScene(placeable, 8) }
    }
    if (path.includes('/tactical-scenes/scene-a')) {
      const t = options.freshToken || token('t1')
      return { ok: true, scene_id: 'scene-a', scene: gmScene(t, options.freshRevision ?? 9), active_scene_ref: null, revision: options.freshRevision ?? 9 }
    }
    throw new Error(`unexpected request: ${method} ${path}`)
  }) as typeof client.api)
  return calls
}

function mountDialog() {
  i18n.global.locale.value = 'en'
  return mount(TokenBindDialog, {
    global: { plugins: [i18n] },
    props: { gameKey: 'g', sceneId: 'scene-a', token: token('t1'), revision: 7 },
  })
}

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
})

describe('TokenBindDialog', () => {
  it('loads participants grouped by kind and shows the current not-imported binding', async () => {
    mockApi()
    const wrapper = mountDialog()
    await flushPromises()
    expect(wrapper.get('[data-testid="token-bind-current"]').text()).toContain('Actor not imported')
    expect(wrapper.find('[data-testid="token-bind-target-player-gm"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="token-bind-target-enemy-goblin-1"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Players')
    expect(wrapper.text()).toContain('Enemies')
  })

  it('binds the selected participant and emits bound with the write response', async () => {
    const calls = mockApi()
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="token-bind-target-player-gm"]').setValue(true)
    await wrapper.get('[data-testid="token-bind-submit"]').trigger('click')
    await flushPromises()
    const patch = calls.find(call => call.method === 'PATCH')!
    expect((patch.body!.placeable as TacticalSceneTokenDocument).actor_ref).toEqual({ source_kind: 'diceframe', source_id: 'g', kind: 'player', id: 'gm', digest: '' })
    expect(patch.body!.expected_revision).toBe(7)
    expect(wrapper.get('[data-testid="token-bind-success"]').text()).toBe('Token binding saved.')
    const emitted = wrapper.emitted('bound')
    expect(emitted).toHaveLength(1)
    expect((emitted![0]![0] as { revision: number }).revision).toBe(8)
  })

  it('unbinds the current actor', async () => {
    const calls = mockApi()
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="token-bind-unbind"]').trigger('click')
    await flushPromises()
    const patch = calls.find(call => call.method === 'PATCH')!
    expect((patch.body!.placeable as TacticalSceneTokenDocument).actor_ref).toBeNull()
    expect(wrapper.get('[data-testid="token-bind-success"]').text()).toBe('Token unbound.')
  })

  it('shows a conflict banner on a stale revision and reloads the current token', async () => {
    const fresh = token('t1', { actor_ref: { source_kind: 'diceframe', source_id: 'g', kind: 'enemy', id: 'goblin-1', digest: '' }, disposition: 0 })
    const calls = mockApi({ stale: true, freshToken: fresh, freshRevision: 9 })
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="token-bind-target-player-gm"]').setValue(true)
    await wrapper.get('[data-testid="token-bind-submit"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="token-bind-conflict"]').text()).toContain('The scene changed elsewhere')
    expect(wrapper.find('[data-testid="token-bind-success"]').exists()).toBe(false)

    await wrapper.get('[data-testid="token-bind-conflict-reload"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="token-bind-conflict"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="token-bind-current"]').text()).toContain('enemy:goblin-1')
    expect(calls.some(call => call.method === 'GET' && call.path.includes('/tactical-scenes/scene-a'))).toBe(true)
  })

  it('disables bind until a participant is selected', async () => {
    mockApi()
    const wrapper = mountDialog()
    await flushPromises()
    expect(wrapper.get('[data-testid="token-bind-submit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="token-bind-target-enemy-goblin-1"]').setValue(true)
    expect(wrapper.get('[data-testid="token-bind-submit"]').attributes('disabled')).toBeUndefined()
  })

  it('keeps its working state when the parent re-reads the board after a bind', async () => {
    mockApi()
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="token-bind-target-player-gm"]').setValue(true)
    await wrapper.get('[data-testid="token-bind-submit"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="token-bind-success"]').text()).toBe('Token binding saved.')
    // The parent board projection refreshes (revision prop advances); the dialog
    // must not revert to the stale opening snapshot or drop the success state.
    await wrapper.setProps({ revision: 8 })
    await flushPromises()
    expect(wrapper.get('[data-testid="token-bind-success"]').text()).toBe('Token binding saved.')
    expect(wrapper.get('[data-testid="token-bind-current"]').text()).toContain('player:gm')
  })
})