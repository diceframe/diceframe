import { afterEach, describe, expect, it, vi } from 'vitest'
import { analyzeStoryboard, generateCurrentRoundImage, generatedImageUrl } from '../src/api/generatedImages'

afterEach(() => {
  localStorage.clear()
  location.hash = ''
  vi.unstubAllGlobals()
})

describe('generated images', () => {
  it('sends fixed counts and defaults to combined avatar references', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(
      JSON.stringify({ ok: true, asset_id: 'asset-1', panels: [] }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )))
    vi.stubGlobal('fetch', fetchMock)
    await analyzeStoryboard('web|room|bot', 3, 6)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ round: 3, panel_count: 6 })
    await generateCurrentRoundImage('web|room|bot', {
      prompt: 'scene', panelCount: 6, useAvatarReferences: true,
    })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({
      panel_count: 6, use_avatar_references: true, combine_avatar_references: true,
    })
    await generateCurrentRoundImage('web|room|bot', { prompt: 'scene', combineAvatarReferences: false })
    expect(JSON.parse(fetchMock.mock.calls[2][1].body).combine_avatar_references).toBe(false)
  })

  it('loads game images through the scoped endpoint with application auth', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('image', {
      status: 200,
      headers: { 'Content-Type': 'image/webp' },
    }))
    const createObjectURL = vi.fn().mockReturnValue('blob:generated-image')
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL: vi.fn() })
    localStorage.setItem('trpg_access_token', 'owner-token')

    const url = await generatedImageUrl('asset-1', 'web|room|bot')

    expect(url).toBe('blob:generated-image')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/games/web%7Croom%7Cbot/generated-images/asset-1')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer owner-token')
  })

  it('derives the game endpoint from a player share hash', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('image', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('URL', { createObjectURL: vi.fn().mockReturnValue('blob:share'), revokeObjectURL: vi.fn() })
    location.hash = '#/play?game=web%7Croom%7Cbot&user=player-1'

    await generatedImageUrl('asset-2')

    expect(fetchMock.mock.calls[0][0]).toContain('/api/games/web%7Croom%7Cbot/generated-images/asset-2?')
    expect(fetchMock.mock.calls[0][0]).toContain('user=player-1')
  })
})
