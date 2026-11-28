import { afterEach, describe, expect, expectTypeOf, it, vi } from 'vitest'
import * as client from '../src/api/client'
import {
  TACTICAL_SCENE_ERROR_CODES,
  fetchActiveTacticalScene,
  fetchTacticalScene,
  fetchTacticalScenes,
  type TacticalSceneDocument,
  type TacticalSceneSeatProjection,
  type TacticalSceneTokenProjection,
  type TacticalSceneUpdatedEvent,
} from '../src/api/tacticalScene'
import { messages } from '../src/i18n'

afterEach(() => { vi.restoreAllMocks() })

describe('tactical scene API client', () => {
  it('reads the list, one scene and the active scene through api()', async () => {
    const listResponse = { ok: true, scenes: [], active_scene_ref: null, revision: 0 }
    const readResponse = { ok: true, scene_id: 'scene-a', scene: { canonical_id: 'scene-a' }, active_scene_ref: 'scene-a', revision: 2 }
    const apiMock = vi.fn()
      .mockResolvedValueOnce(listResponse)
      .mockResolvedValueOnce(readResponse)
      .mockResolvedValueOnce(readResponse)
    vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)

    await expect(fetchTacticalScenes('game one')).resolves.toBe(listResponse)
    await expect(fetchTacticalScene('game one', 'scene/a')).resolves.toBe(readResponse)
    await expect(fetchActiveTacticalScene('game one')).resolves.toBe(readResponse)
    expect(apiMock).toHaveBeenNthCalledWith(1, '/games/game%20one/tactical-scenes')
    expect(apiMock).toHaveBeenNthCalledWith(2, '/games/game%20one/tactical-scenes/scene%2Fa')
    expect(apiMock).toHaveBeenNthCalledWith(3, '/games/game%20one/tactical-scene')
  })

  it('registers localized copy for every declared error code in both core locales', () => {
    const en = messages.en.apiErrors as Record<string, string>
    const zh = messages['zh-CN'].apiErrors as Record<string, string>
    for (const code of TACTICAL_SCENE_ERROR_CODES) {
      expect(en[code], `en apiErrors.${code}`).toBeTruthy()
      expect(zh[code], `zh-CN apiErrors.${code}`).toBeTruthy()
    }
  })
})

describe('tactical scene DTO whitelist', () => {
  it('keeps private scene and token fields out of the seat projection', () => {
    expectTypeOf<TacticalSceneSeatProjection>().not.toHaveProperty('ownership')
    expectTypeOf<TacticalSceneSeatProjection>().not.toHaveProperty('flags')
    expectTypeOf<TacticalSceneSeatProjection>().not.toHaveProperty('extensions')
    expectTypeOf<TacticalSceneSeatProjection>().not.toHaveProperty('provenance')
    expectTypeOf<TacticalSceneSeatProjection>().not.toHaveProperty('source_ref')
    expectTypeOf<TacticalSceneSeatProjection>().not.toHaveProperty('external_ids')
    expectTypeOf<TacticalSceneTokenProjection>().not.toHaveProperty('hidden')
    expectTypeOf<TacticalSceneTokenProjection>().not.toHaveProperty('sight')
    expectTypeOf<TacticalSceneTokenProjection>().not.toHaveProperty('delta')
    expectTypeOf<TacticalSceneTokenProjection>().toHaveProperty('actor_ref')
    expectTypeOf<TacticalSceneDocument>().toHaveProperty('ownership')
    expectTypeOf<TacticalSceneDocument['placeables']>().toHaveProperty('notes')
    expectTypeOf<TacticalSceneSeatProjection['placeables']>().not.toHaveProperty('notes')
    expectTypeOf<TacticalSceneUpdatedEvent['projection']>().toEqualTypeOf<
      TacticalSceneDocument | TacticalSceneSeatProjection | null
    >()
  })
})