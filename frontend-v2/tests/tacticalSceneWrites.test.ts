import { afterEach, describe, expect, it, vi } from 'vitest'
import * as client from '../src/api/client'
import {
  activateTacticalScene,
  createTacticalScene,
  createTacticalScenePlaceable,
  deleteTacticalScene,
  deleteTacticalScenePlaceable,
  updateTacticalScene,
  updateTacticalScenePlaceable,
  type TacticalSceneDocument,
  type TacticalSceneTokenDocument,
} from '../src/api/tacticalScene'

function scene(): TacticalSceneDocument {
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
    dimensions: { width: 100, height: 100, padding: 0, offset_x: 0, offset_y: 0, initial_view: { x: 0, y: 0, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: '', color: '', alpha: 0.2, distance: 5 },
    environment: { darkness: 0, global_illumination: false, fog: { exploration: false, reset_policy: '' }, weather: '' },
    placeables: { tokens: {}, tiles: {}, walls: {}, lights: {}, sounds: {}, notes: {}, drawings: {}, templates: {}, regions: {} },
    levels: [],
    flags: {},
    extensions: {},
    provenance: {},
    revision: 0,
  }
}

function token(): TacticalSceneTokenDocument {
  return {
    schema_version: 1,
    kind: 'token',
    canonical_id: 't1',
    actor_ref: null,
    position: { x: 10, y: 20, elevation: 0, level: 0 },
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
  }
}

const writeResponse = {
  ok: true,
  operation: 'tactical_scene_update',
  scene_id: 'scene-a',
  revision: 6,
  active_scene_ref: null,
  scene: scene(),
}

afterEach(() => { vi.restoreAllMocks() })

describe('tactical scene write API client', () => {
  it('sends every GM write to the contracted route with expected_revision in the body', async () => {
    const apiMock = vi.fn().mockResolvedValue(writeResponse)
    vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
    const document = scene()
    const placeable = token()

    await createTacticalScene('game one', document, 5)
    await updateTacticalScene('game one', 'scene/a', document, 5)
    await deleteTacticalScene('game one', 'scene/a', 5)
    await activateTacticalScene('game one', 'scene/a', 5)
    await createTacticalScenePlaceable('game one', 'scene/a', 'tokens', placeable, 5)
    await updateTacticalScenePlaceable('game one', 'scene/a', 'tokens', 't/1', placeable, 5)
    await deleteTacticalScenePlaceable('game one', 'scene/a', 'tokens', 't/1', 5)

    const base = '/games/game%20one/tactical-scenes/scene%2Fa'
    expect(apiMock).toHaveBeenNthCalledWith(1, '/games/game%20one/tactical-scenes', {
      method: 'POST', body: JSON.stringify({ scene: document, expected_revision: 5 }),
    })
    expect(apiMock).toHaveBeenNthCalledWith(2, base, {
      method: 'PATCH', body: JSON.stringify({ scene: document, expected_revision: 5 }),
    })
    expect(apiMock).toHaveBeenNthCalledWith(3, base, {
      method: 'DELETE', body: JSON.stringify({ expected_revision: 5 }),
    })
    expect(apiMock).toHaveBeenNthCalledWith(4, `${base}/activate`, {
      method: 'POST', body: JSON.stringify({ expected_revision: 5 }),
    })
    expect(apiMock).toHaveBeenNthCalledWith(5, `${base}/placeables/tokens`, {
      method: 'POST', body: JSON.stringify({ placeable, expected_revision: 5 }),
    })
    expect(apiMock).toHaveBeenNthCalledWith(6, `${base}/placeables/tokens/t%2F1`, {
      method: 'PATCH', body: JSON.stringify({ placeable, expected_revision: 5 }),
    })
    expect(apiMock).toHaveBeenNthCalledWith(7, `${base}/placeables/tokens/t%2F1`, {
      method: 'DELETE', body: JSON.stringify({ expected_revision: 5 }),
    })
  })

  it('propagates the stable error code from a rejected write', async () => {
    const apiMock = vi.fn().mockRejectedValue(new client.ApiError('raw', 409, 'tactical_scene_stale_revision'))
    vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
    await expect(updateTacticalScene('g', 'scene-a', scene(), 5)).rejects.toMatchObject({
      status: 409, code: 'tactical_scene_stale_revision',
    })
  })
})