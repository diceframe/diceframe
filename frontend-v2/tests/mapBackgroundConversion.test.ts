import { afterEach, describe, expect, it, vi } from 'vitest'
import * as client from '../src/api/client'
import {
  MAP_BACKGROUND_CONVERSION_ERROR_CODES,
  convertMapBackgroundToTacticalScene,
} from '../src/api/tacticalScene'
import { messages } from '../src/i18n'

afterEach(() => { vi.restoreAllMocks() })

describe('map background conversion API client', () => {
  it('posts the explicit confirmation to the from-map-background route', async () => {
    const response = { ok: true, created: true, scene_id: 'mapbg.upload.abc', revision: 1, active_scene_ref: null, scene: { canonical_id: 'mapbg.upload.abc' } }
    const apiMock = vi.fn().mockResolvedValue(response)
    vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)

    await expect(convertMapBackgroundToTacticalScene('game one')).resolves.toBe(response)
    expect(apiMock).toHaveBeenCalledWith('/games/game%20one/tactical-scenes/from-map-background', {
      method: 'POST',
      body: JSON.stringify({ confirm: true }),
    })
  })

  it('registers localized copy for every conversion error code in both core locales', () => {
    const en = messages.en.apiErrors as Record<string, string>
    const zh = messages['zh-CN'].apiErrors as Record<string, string>
    for (const code of MAP_BACKGROUND_CONVERSION_ERROR_CODES) {
      expect(en[code], `en apiErrors.${code}`).toBeTruthy()
      expect(zh[code], `zh-CN apiErrors.${code}`).toBeTruthy()
    }
  })
})