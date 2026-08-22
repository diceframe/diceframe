import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  mapBackgroundChoice,
  mapBackgroundSelection,
  resolveMapBackgroundAsset,
  revokeMapBackgroundAsset,
  validateMapBackgroundFile,
} from '../src/api/mapBackgrounds'
import type { MapData } from '../src/api/types'

afterEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('map backgrounds', () => {
  it('converts persisted selections to picker choices and back', () => {
    expect(mapBackgroundChoice({ kind: 'builtin', id: 'occult-town-v1' }))
      .toBe('builtin:occult-town-v1')
    expect(mapBackgroundSelection('builtin:cyber-city-v1'))
      .toEqual({ kind: 'builtin', id: 'cyber-city-v1' })
    expect(mapBackgroundChoice({ kind: 'generated', asset_id: 'asset-1' }))
      .toBe('generated:asset-1')
    expect(mapBackgroundSelection('generated:asset-2'))
      .toEqual({ kind: 'generated', asset_id: 'asset-2' })
    expect(mapBackgroundSelection('none')).toEqual({ kind: 'none' })
    expect(mapBackgroundSelection('unexpected')).toEqual({ kind: 'auto' })
  })

  it('rejects unsupported and oversized uploads before sending them', () => {
    expect(() => validateMapBackgroundFile(new File(['x'], 'map.gif', { type: 'image/gif' })))
      .toThrow('unsupported-map-background-type')
    const oversized = new File(
      [new Uint8Array(8 * 1024 * 1024 + 1)],
      'map.png',
      { type: 'image/png' },
    )
    expect(() => validateMapBackgroundFile(oversized)).toThrow('map-background-too-large')
  })

  it('loads protected game assets with the application token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('map', {
      status: 200,
      headers: { 'Content-Type': 'image/webp' },
    }))
    const createObjectURL = vi.fn().mockReturnValue('blob:map-background')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    localStorage.setItem('trpg_access_token', 'owner-token')
    const map: MapData = {
      locations: [],
      active_map: {
        id: 'custom',
        name: 'Custom',
        mode: 'graph',
        background: {
          id: 'asset',
          url: '/api/games/save-1/map-background-asset/asset',
        },
      },
    }

    const resolved = await resolveMapBackgroundAsset(map)

    expect(resolved.active_map?.background?.url).toBe('blob:map-background')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer owner-token')
    revokeMapBackgroundAsset(resolved)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:map-background')
  })
})
