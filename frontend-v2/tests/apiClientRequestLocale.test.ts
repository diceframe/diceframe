import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, authHeaders } from '@/api/client'
import { i18n } from '@/i18n'

function okFetch() {
  const fetchMock = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ ok: true }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  ))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('API client request language', () => {
  const initialLocale = i18n.global.locale.value

  afterEach(() => {
    i18n.global.locale.value = initialLocale
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('asks the backend for the UI language the user picked, not the browser default', async () => {
    i18n.global.locale.value = 'en'
    expect(authHeaders().get('Accept-Language')).toBe('en')

    const fetchMock = okFetch()
    i18n.global.locale.value = 'zh-CN'
    await api('/config')
    expect((fetchMock.mock.calls[0][1].headers as Headers).get('Accept-Language')).toBe('zh-CN')
  })

  it('keeps a caller-supplied language', () => {
    i18n.global.locale.value = 'en'
    expect(authHeaders({ 'Accept-Language': 'ja' }).get('Accept-Language')).toBe('ja')
  })

  it('also negotiates cross-origin: the header is CORS-safelisted, so no preflight allowance is needed', () => {
    vi.stubGlobal('__DF_STANDALONE__', true)
    localStorage.setItem('trpg_backend_url', 'https://table.example.com')
    i18n.global.locale.value = 'en'
    expect(authHeaders().get('Accept-Language')).toBe('en')
  })
})
