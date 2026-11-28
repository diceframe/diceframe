import { ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import type {
  TacticalSceneDocument,
  TacticalSceneTokenDocument,
  TacticalSceneWriteResponse,
} from '../src/api/tacticalScene'
import { useSceneEditor } from '../src/features/tactical-scene/useSceneEditor'

function gmScene(): TacticalSceneDocument {
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
    dimensions: { width: 1400, height: 1000, padding: 0.25, offset_x: 0, offset_y: 0, initial_view: { x: 700, y: 500, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: '', color: '', alpha: 0.2, distance: 5 },
    environment: { darkness: 0, global_illumination: false, fog: { exploration: false, reset_policy: '' }, weather: '' },
    placeables: { tokens: {}, tiles: {}, walls: {}, lights: {}, sounds: {}, notes: {}, drawings: {}, templates: {}, regions: {} },
    levels: [],
    flags: {},
    extensions: {},
    provenance: {},
    revision: 1,
  }
}

function token(id: string, over: Partial<TacticalSceneTokenDocument> = {}): TacticalSceneTokenDocument {
  return {
    schema_version: 1,
    kind: 'token',
    canonical_id: id,
    actor_ref: null,
    position: { x: 100, y: 200, elevation: 0, level: 0 },
    size: { width: 1, height: 1 },
    rotation: 0,
    texture: null,
    ring: {},
    alpha: 1,
    display: {},
    disposition: 0,
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

function readResponse(scene: TacticalSceneDocument, revision: number) {
  return { ok: true as const, scene_id: scene.canonical_id, scene, active_scene_ref: null, revision }
}

function writeResponse(scene: TacticalSceneDocument | null, revision: number): TacticalSceneWriteResponse {
  return { ok: true, operation: 'tactical_scene_update', scene_id: 'scene-a', revision, active_scene_ref: null, scene }
}

function setup(apiMock: ReturnType<typeof vi.fn>) {
  vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
  i18n.global.locale.value = 'en'
  const editor = useSceneEditor(ref('g'), ref('scene-a'))
  return editor
}

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
})

describe('useSceneEditor draft isolation', () => {
  it('loads the GM document into a separate draft and tracks the slot revision', async () => {
    const scene = gmScene()
    scene.placeables.tokens['t1'] = token('t1')
    const editor = setup(vi.fn().mockResolvedValue(readResponse(scene, 5)))
    await editor.load()
    expect(editor.baseRevision.value).toBe(5)
    expect(editor.draft.value?.placeables.tokens['t1']?.position.x).toBe(100)
    expect(editor.dirty.value).toBe(false)

    editor.mutate(draft => { draft.placeables.tokens['t1']!.position.x = 999 })
    expect(editor.base.value?.placeables.tokens['t1']?.position.x).toBe(100)
    expect(editor.dirty.value).toBe(true)
  })

  it('refuses to edit a seat projection (no ownership) and surfaces the GM-only error', async () => {
    const seat = { ...gmScene() } as Record<string, unknown>
    delete seat.ownership
    const editor = setup(vi.fn().mockResolvedValue(readResponse(seat as unknown as TacticalSceneDocument, 5)))
    await editor.load()
    expect(editor.draft.value).toBeNull()
    expect(editor.loadError.value).toContain('do not have access')
  })

  it('undoes draft mutations and discard resets to the authoritative base', async () => {
    const editor = setup(vi.fn().mockResolvedValue(readResponse(gmScene(), 5)))
    await editor.load()
    editor.mutate(draft => { draft.display_name = 'Renamed' })
    expect(editor.draft.value?.display_name).toBe('Renamed')
    expect(editor.canUndo.value).toBe(true)
    editor.undo()
    expect(editor.draft.value?.display_name).toBe('Chapel')
    expect(editor.dirty.value).toBe(false)

    editor.mutate(draft => { draft.display_name = 'Again' })
    editor.discard()
    expect(editor.draft.value?.display_name).toBe('Chapel')
    expect(editor.canUndo.value).toBe(false)
  })
})

describe('useSceneEditor placeable operations', () => {
  it('adds, selects, moves and removes placeables in the draft only', async () => {
    const editor = setup(vi.fn().mockResolvedValue(readResponse(gmScene(), 5)))
    await editor.load()
    editor.addPlaceable('tokens', token('t1'))
    expect(editor.selection.value).toEqual([{ collection: 'tokens', id: 't1' }])
    expect(editor.base.value?.placeables.tokens['t1']).toBeUndefined()

    editor.beginGesture()
    editor.movePlaceables([{ collection: 'tokens', id: 't1' }], 30, -20)
    expect(editor.draft.value?.placeables.tokens['t1']?.position).toMatchObject({ x: 130, y: 180 })
    editor.undo()
    expect(editor.draft.value?.placeables.tokens['t1']?.position).toMatchObject({ x: 100, y: 200 })

    editor.removePlaceables([{ collection: 'tokens', id: 't1' }])
    expect(editor.draft.value?.placeables.tokens['t1']).toBeUndefined()
    expect(editor.selection.value).toEqual([])
  })

  it('never moves locked tokens or opaque placeables, and moves wall endpoints', async () => {
    const scene = gmScene()
    scene.placeables.tokens['locked'] = token('locked', { locked: true })
    scene.placeables.walls['w1'] = {
      schema_version: 1, kind: 'wall', canonical_id: 'w1',
      endpoints: { x0: 0, y0: 0, x1: 100, y1: 0 },
      direction: 0, door: 0, door_state: 0,
      restrictions: { move: 0, sight: 0, sound: 0, light: 0 },
      external_ids: {}, flags: {}, extensions: {}, provenance: {},
    }
    scene.placeables.tiles['tile1'] = { canonical_id: 'tile1', x: 5, y: 5, external_ids: {}, flags: {}, extensions: {}, provenance: {} }
    const editor = setup(vi.fn().mockResolvedValue(readResponse(scene, 5)))
    await editor.load()
    editor.movePlaceables([
      { collection: 'tokens', id: 'locked' },
      { collection: 'walls', id: 'w1' },
      { collection: 'tiles', id: 'tile1' },
    ], 10, 10)
    expect(editor.draft.value?.placeables.tokens['locked']?.position).toMatchObject({ x: 100, y: 200 })
    expect(editor.draft.value?.placeables.walls['w1']?.endpoints).toEqual({ x0: 10, y0: 10, x1: 110, y1: 10 })
    expect(editor.draft.value?.placeables.tiles['tile1']).toMatchObject({ x: 5, y: 5 })
  })

  it('clamps inspector fields to the typed contract', async () => {
    const scene = gmScene()
    scene.placeables.tokens['t1'] = token('t1')
    scene.placeables.lights['l1'] = {
      schema_version: 1, kind: 'light', canonical_id: 'l1',
      position: { x: 0, y: 0 }, rotation: 0, elevation: 0,
      radius: { dim: 10, bright: 5 }, color: '', intensity: 0.5, angle: 360,
      hidden: false, provides_vision: false, blocked_by_walls: false,
      external_ids: {}, flags: {}, extensions: {}, provenance: {},
    }
    const editor = setup(vi.fn().mockResolvedValue(readResponse(scene, 5)))
    await editor.load()
    editor.setPlaceableField({ collection: 'tokens', id: 't1' }, 'width', 0)
    expect(editor.draft.value?.placeables.tokens['t1']?.size.width).toBe(0.25)
    editor.setPlaceableField({ collection: 'lights', id: 'l1' }, 'intensity', 5)
    expect(editor.draft.value?.placeables.lights['l1']?.intensity).toBe(1)
    editor.setPlaceableField({ collection: 'lights', id: 'l1' }, 'color', 'not-a-color')
    expect(editor.draft.value?.placeables.lights['l1']?.color).toBe('')
    editor.setPlaceableField({ collection: 'lights', id: 'l1' }, 'color', '#ffaa00')
    expect(editor.draft.value?.placeables.lights['l1']?.color).toBe('#ffaa00')
    editor.setPlaceableField({ collection: 'tokens', id: 't1' }, 'hidden', true)
    expect(editor.draft.value?.placeables.tokens['t1']?.hidden).toBe(true)
  })

  it('reorders layer keys within a collection without inventing a z-order field', async () => {
    const scene = gmScene()
    scene.placeables.tokens['a'] = token('a')
    scene.placeables.tokens['b'] = token('b')
    scene.placeables.tokens['c'] = token('c')
    const editor = setup(vi.fn().mockResolvedValue(readResponse(scene, 5)))
    await editor.load()
    editor.reorderPlaceable({ collection: 'tokens', id: 'a' }, 'forward')
    expect(Object.keys(editor.draft.value!.placeables.tokens)).toEqual(['b', 'a', 'c'])
    editor.reorderPlaceable({ collection: 'tokens', id: 'a' }, 'forward')
    expect(Object.keys(editor.draft.value!.placeables.tokens)).toEqual(['b', 'c', 'a'])
    editor.reorderPlaceable({ collection: 'tokens', id: 'a' }, 'forward')
    expect(Object.keys(editor.draft.value!.placeables.tokens)).toEqual(['b', 'c', 'a'])
    expect(editor.draft.value!.placeables.tokens['a']).not.toHaveProperty('z')
  })

  it('supports additive multi-selection', async () => {
    const scene = gmScene()
    scene.placeables.tokens['a'] = token('a')
    scene.placeables.tokens['b'] = token('b')
    const editor = setup(vi.fn().mockResolvedValue(readResponse(scene, 5)))
    await editor.load()
    editor.select({ collection: 'tokens', id: 'a' })
    editor.select({ collection: 'tokens', id: 'b' }, true)
    expect(editor.selection.value).toHaveLength(2)
    editor.select({ collection: 'tokens', id: 'a' }, true)
    expect(editor.selection.value).toEqual([{ collection: 'tokens', id: 'b' }])
    editor.selectMany([{ collection: 'tokens', id: 'a' }, { collection: 'tokens', id: 'b' }])
    expect(editor.selection.value).toHaveLength(2)
  })
})

describe('useSceneEditor save and conflict', () => {
  it('saves the whole draft with expected_revision and adopts the committed projection', async () => {
    const scene = gmScene()
    const saved = gmScene()
    saved.display_name = 'Renamed'
    saved.revision = 2
    const apiMock = vi.fn((path: string, init?: RequestInit) => {
      if (!init?.method) return Promise.resolve(readResponse(scene, 5))
      expect(init.method).toBe('PATCH')
      expect(path).toBe('/games/g/tactical-scenes/scene-a')
      const body = JSON.parse(String(init.body)) as { scene: TacticalSceneDocument; expected_revision: number }
      expect(body.expected_revision).toBe(5)
      expect(body.scene.display_name).toBe('Renamed')
      return Promise.resolve(writeResponse(saved, 6))
    })
    const editor = setup(apiMock)
    await editor.load()
    editor.mutate(draft => { draft.display_name = 'Renamed' })
    expect(await editor.save()).toBe(true)
    expect(editor.baseRevision.value).toBe(6)
    expect(editor.base.value?.display_name).toBe('Renamed')
    expect(editor.draft.value?.display_name).toBe('Renamed')
    expect(editor.dirty.value).toBe(false)
    expect(editor.canUndo.value).toBe(false)
    expect(editor.conflict.value).toBe(false)
  })

  it('flags a stale revision as a conflict and keeps the draft untouched', async () => {
    const scene = gmScene()
    const apiMock = vi.fn((path: string, init?: RequestInit) => {
      if (!init?.method) return Promise.resolve(readResponse(scene, 5))
      return Promise.reject(new ApiError('raw', 409, 'tactical_scene_stale_revision'))
    })
    const editor = setup(apiMock)
    await editor.load()
    editor.mutate(draft => { draft.display_name = 'Mine' })
    expect(await editor.save()).toBe(false)
    expect(editor.conflict.value).toBe(true)
    expect(editor.saveErrorCode.value).toBe('tactical_scene_stale_revision')
    expect(editor.saveError.value).toContain('updated elsewhere')
    expect(editor.draft.value?.display_name).toBe('Mine')
    expect(editor.baseRevision.value).toBe(5)
  })

  it('reload after a conflict discards the draft and re-reads authority', async () => {
    const scene = gmScene()
    const fresh = gmScene()
    fresh.display_name = 'Theirs'
    let revision = 5
    const apiMock = vi.fn((path: string, init?: RequestInit) => {
      if (!init?.method) return Promise.resolve(readResponse(revision === 5 ? scene : fresh, revision))
      return Promise.reject(new ApiError('raw', 409, 'tactical_scene_stale_revision'))
    })
    const editor = setup(apiMock)
    await editor.load()
    editor.mutate(draft => { draft.display_name = 'Mine' })
    await editor.save()
    expect(editor.conflict.value).toBe(true)
    revision = 9
    await editor.load()
    expect(editor.conflict.value).toBe(false)
    expect(editor.baseRevision.value).toBe(9)
    expect(editor.draft.value?.display_name).toBe('Theirs')
    expect(editor.dirty.value).toBe(false)
  })

  it('explicit overwrite re-reads the revision and saves the kept draft', async () => {
    const scene = gmScene()
    const fresh = gmScene()
    fresh.display_name = 'Theirs'
    const saved = gmScene()
    saved.display_name = 'Mine'
    let revision = 5
    const apiMock = vi.fn((path: string, init?: RequestInit) => {
      if (!init?.method) return Promise.resolve(readResponse(revision === 5 ? scene : fresh, revision))
      const body = JSON.parse(String(init.body)) as { expected_revision: number; scene: TacticalSceneDocument }
      if (revision === 5) return Promise.reject(new ApiError('raw', 409, 'tactical_scene_stale_revision'))
      expect(body.expected_revision).toBe(9)
      expect(body.scene.display_name).toBe('Mine')
      return Promise.resolve(writeResponse(saved, 10))
    })
    const editor = setup(apiMock)
    await editor.load()
    editor.mutate(draft => { draft.display_name = 'Mine' })
    await editor.save()
    revision = 9
    expect(await editor.saveOverwrite()).toBe(true)
    expect(editor.baseRevision.value).toBe(10)
    expect(editor.draft.value?.display_name).toBe('Mine')
    expect(editor.conflict.value).toBe(false)
  })

  it('surfaces non-conflict save failures with a plain retry path', async () => {
    const scene = gmScene()
    let fail = true
    const apiMock = vi.fn((path: string, init?: RequestInit) => {
      if (!init?.method) return Promise.resolve(readResponse(scene, 5))
      if (fail) return Promise.reject(new ApiError('raw', 500, 'save_failed'))
      return Promise.resolve(writeResponse(scene, 6))
    })
    const editor = setup(apiMock)
    await editor.load()
    editor.mutate(draft => { draft.display_name = 'X' })
    expect(await editor.save()).toBe(false)
    expect(editor.conflict.value).toBe(false)
    expect(editor.saveErrorCode.value).toBe('save_failed')
    fail = false
    expect(await editor.save()).toBe(true)
    expect(editor.saveError.value).toBe('')
  })

  it('activates and deletes with expected_revision', async () => {
    const scene = gmScene()
    const calls: { path: string; method: string; expected: number }[] = []
    const apiMock = vi.fn((path: string, init?: RequestInit) => {
      if (!init?.method) return Promise.resolve(readResponse(scene, 5))
      calls.push({ path, method: init.method, expected: (JSON.parse(String(init.body)) as { expected_revision: number }).expected_revision })
      return Promise.resolve(writeResponse(scene, 6))
    })
    const editor = setup(apiMock)
    await editor.load()
    expect(await editor.activate()).toBe(true)
    expect(await editor.deleteScene()).toBe(true)
    expect(calls).toEqual([
      { path: '/games/g/tactical-scenes/scene-a/activate', method: 'POST', expected: 5 },
      { path: '/games/g/tactical-scenes/scene-a', method: 'DELETE', expected: 6 },
    ])
  })
})