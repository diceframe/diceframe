import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import { ApiError } from '../src/api/client'
import { readCurrentGame, rememberCurrentGame } from '../src/stores/gameContext'
import LogsView from '../src/features/admin/LogsView.vue'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
}))

vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api }
})

describe('LogsView', () => {
  beforeEach(() => {
    const store = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => { store.set(key, String(value)) },
      removeItem: (key: string) => { store.delete(key) },
      clear: () => { store.clear() },
    })
    mocks.api.mockReset()
    i18n.global.locale.value = 'zh-CN'
  })

  it('clears a stale save and shows a friendly empty state', async () => {
    rememberCurrentGame('missing-game')
    mocks.api.mockRejectedValue(new ApiError('not found', 404))

    const wrapper = mount(LogsView, { global: { plugins: [i18n] } })
    await flushPromises()

    expect(readCurrentGame()).toBe('')
    expect(wrapper.text()).toContain('还没有冒险记录')
    expect(wrapper.text().toLowerCase()).not.toContain('not found')
  })
})
