import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { i18n } from '../src/i18n'
import StartupUpdateCheck from '../src/components/common/StartupUpdateCheck.vue'

const mocks = vi.hoisted(() => ({
  checkForUpdates: vi.fn(),
  info: vi.fn(),
  destroy: vi.fn(),
}))

vi.mock('../src/composables/useUpdateCheck', () => ({
  useUpdateCheck: () => ({
    checkForUpdates: mocks.checkForUpdates,
  }),
}))

vi.mock('../src/composables/useNaiveBridge', () => ({
  getDialog: () => ({ info: mocks.info }),
}))

describe('StartupUpdateCheck', () => {
  beforeEach(() => {
    const store = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => { store.set(key, String(value)) },
      removeItem: (key: string) => { store.delete(key) },
      clear: () => { store.clear() },
    })
    localStorage.clear()
    mocks.checkForUpdates.mockReset()
    mocks.info.mockReset()
    mocks.destroy.mockReset()
    mocks.info.mockReturnValue({ destroy: mocks.destroy })
    i18n.global.locale.value = 'zh-CN'
  })

  it('opens the update area in Settings instead of the release page', async () => {
    mocks.checkForUpdates.mockResolvedValue({
      ok: true,
      update_available: true,
      latest: {
        version: '1.6.2',
        tag_name: 'v1.6.2',
        body: '更新说明',
        html_url: 'https://github.com/diceframe/diceframe/releases/tag/v1.6.2',
      },
    })
    const emptyView = defineComponent({ template: '<div />' })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/overview', name: 'overview', component: emptyView },
        { path: '/settings', name: 'settings', component: emptyView },
      ],
    })
    await router.push({ name: 'overview' })
    await router.isReady()

    mount(StartupUpdateCheck, {
      global: { plugins: [i18n, router] },
    })
    await flushPromises()

    expect(mocks.info).toHaveBeenCalledOnce()
    const dialog = mocks.info.mock.calls[0][0]
    expect(dialog.positiveText).toBe('前往设置更新')

    await dialog.onPositiveClick()
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('settings')
    expect(router.currentRoute.value.query).toEqual({
      section: 'about',
      focus: 'update',
    })
  })

  it('closes an existing update dialog after entering the login page', async () => {
    mocks.checkForUpdates.mockResolvedValue({
      ok: true,
      update_available: true,
      latest: {
        version: '9.9.9',
        tag_name: 'v9.9.9',
        body: '更新说明',
      },
    })
    const emptyView = defineComponent({ template: '<div />' })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/overview', name: 'overview', component: emptyView },
        { path: '/login', name: 'login', component: emptyView },
      ],
    })
    await router.push({ name: 'overview' })
    await router.isReady()

    mount(StartupUpdateCheck, {
      global: { plugins: [i18n, router] },
    })
    await flushPromises()

    expect(mocks.info).toHaveBeenCalledOnce()
    await router.push({ name: 'login' })
    await flushPromises()

    expect(mocks.destroy).toHaveBeenCalledOnce()
    expect(mocks.checkForUpdates).toHaveBeenCalledOnce()
  })
})
