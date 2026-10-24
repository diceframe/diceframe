import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n, type Locale } from '../src/i18n'

const { apiMock, errorMessageMock } = vi.hoisted(() => ({
  apiMock: vi.fn(),
  errorMessageMock: vi.fn((error: unknown) => String(error)),
}))

vi.mock('../src/api/client', () => ({
  api: apiMock,
  errorMessage: errorMessageMock,
}))

import GettingStartedCard from '../src/features/onboarding/GettingStartedCard.vue'

const routes = [
  { name: 'settings', path: '/settings', component: { template: '<div />' } },
  { name: 'create', path: '/create', component: { template: '<div />' } },
]

function router() {
  return createRouter({ history: createMemoryHistory(), routes })
}

function mountCard(props: { hasGames?: boolean } = {}) {
  return mount(GettingStartedCard, {
    props,
    global: { plugins: [i18n, router()] },
  })
}

describe('GettingStartedCard', () => {
  beforeEach(() => {
    localStorage.clear()
    apiMock.mockReset()
    errorMessageMock.mockClear()
    i18n.global.locale.value = 'zh-CN'
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('opens for a first visit and collapses to a row when saves already exist', async () => {
    const firstVisit = mountCard()
    expect(firstVisit.find('[data-testid="getting-started-card"]').exists()).toBe(true)
    expect(firstVisit.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    firstVisit.unmount()

    const returning = mountCard({ hasGames: true })
    expect(returning.find('[data-testid="getting-started-collapsed"]').exists()).toBe(true)
    expect(returning.find('[data-testid="getting-started-card"]').exists()).toBe(false)
    await returning.get('[data-testid="getting-started-expand"]').trigger('click')
    expect(returning.find('[data-testid="getting-started-card"]').exists()).toBe(true)
  })

  it('does not call config until the user starts, then shows the API status', async () => {
    apiMock.mockResolvedValue({ ai_providers: [], llm_provider_ref: '', model: '' })
    const wrapper = mountCard()
    expect(apiMock).not.toHaveBeenCalled()
    await wrapper.get('[data-testid="getting-started-start"]').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/config')
    expect(wrapper.get('[data-testid="getting-started-settings"]').attributes('href')).toContain('/settings?section=api')
    expect(wrapper.get('[data-testid="getting-started-create"]').attributes('href')).toContain('/create')
    expect(wrapper.text()).toContain('待配置')
  })

  it('marks only a valid referenced provider and model as configured', async () => {
    apiMock.mockResolvedValue({
      ai_providers: [{ id: 'main', base_url: 'http://localhost:8000/v1' }],
      llm_provider_ref: 'main',
      model: 'gpt-test',
      api_url: 'https://legacy.example.invalid/v1',
      api_key: 'legacy-secret',
    })
    const wrapper = mountCard()
    await wrapper.get('[data-testid="getting-started-start"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已配置')
    expect(wrapper.text()).not.toContain('待配置')
  })

  it.each([
    ['missing reference', { ai_providers: [{ id: 'main', base_url: 'http://localhost:8000/v1' }], llm_provider_ref: '', model: 'gpt-test' }],
    ['stale reference', { ai_providers: [{ id: 'other', base_url: 'http://localhost:8000/v1' }], llm_provider_ref: 'main', model: 'gpt-test' }],
    ['missing model', { ai_providers: [{ id: 'main', base_url: 'http://localhost:8000/v1' }], llm_provider_ref: 'main', model: '' }],
  ])('does not infer readiness from %s or legacy fields', async (_name, config) => {
    apiMock.mockResolvedValue({ ...config, api_url: 'https://legacy.example.invalid/v1', api_key: 'legacy-secret' })
    const wrapper = mountCard()
    await wrapper.get('[data-testid="getting-started-start"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('待配置')
    expect(wrapper.text()).not.toContain('已配置')
  })

  it('reports config failures and retries without showing ready', async () => {
    apiMock.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ ai_providers: [], llm_provider_ref: '', model: '' })
    const wrapper = mountCard()
    await wrapper.get('[data-testid="getting-started-start"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="getting-started-retry"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('已配置')
    await wrapper.get('[data-testid="getting-started-retry"]').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('待配置')
  })

  it('persists collapse across remounts and still toggles when storage is unavailable', async () => {
    const wrapper = mountCard()
    await wrapper.get('[data-testid="getting-started-collapse"]').trigger('click')
    wrapper.unmount()
    const remounted = mountCard()
    expect(remounted.find('[data-testid="getting-started-collapsed"]').exists()).toBe(true)
    const storagePrototype = Object.getPrototypeOf(localStorage) as Storage
    vi.spyOn(storagePrototype, 'getItem').mockImplementation(() => { throw new Error('storage denied') })
    vi.spyOn(storagePrototype, 'setItem').mockImplementation(() => { throw new Error('storage denied') })
    const unavailable = mountCard()
    expect(unavailable.find('[data-testid="getting-started-card"]').exists()).toBe(true)
    await unavailable.get('[data-testid="getting-started-collapse"]').trigger('click')
    expect(unavailable.find('[data-testid="getting-started-collapsed"]').exists()).toBe(true)
    await unavailable.get('[data-testid="getting-started-expand"]').trigger('click')
    expect(unavailable.find('[data-testid="getting-started-card"]').exists()).toBe(true)
  })

  it('keeps keyboard focus on the recovery action while collapsing and reopening', async () => {
    const wrapper = mount(GettingStartedCard, {
      props: { hasGames: true },
      attachTo: document.body,
      global: { plugins: [i18n, router()] },
    })
    await wrapper.get('[data-testid="getting-started-expand"]').trigger('click')
    await nextTick()
    await wrapper.get('[data-testid="getting-started-collapse"]').trigger('click')
    await nextTick()
    expect(document.activeElement).toBe(wrapper.get('[data-testid="getting-started-expand"]').element)
    await wrapper.get('[data-testid="getting-started-expand"]').trigger('click')
    await nextTick()
    expect(document.activeElement).toBe(wrapper.get('[data-testid="getting-started-start"]').element)
    wrapper.unmount()
  })

  it('re-reads config when a solo start is reopened', async () => {
    apiMock
      .mockResolvedValueOnce({ ai_providers: [], llm_provider_ref: '', model: '' })
      .mockResolvedValueOnce({ ai_providers: [{ id: 'main', base_url: 'http://localhost:8000/v1' }], llm_provider_ref: 'main', model: 'gpt-test' })
    const wrapper = mountCard()
    await wrapper.get('[data-testid="getting-started-start"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('待配置')
    await wrapper.get('[data-testid="getting-started-collapse"]').trigger('click')
    await wrapper.get('[data-testid="getting-started-expand"]').trigger('click')
    await wrapper.get('[data-testid="getting-started-start"]').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('已配置')
  })

  it('offers an independent dismiss action in both expanded and collapsed states', async () => {
    const expanded = mountCard()
    expect(expanded.find('[data-testid="getting-started-close"]').exists()).toBe(true)
    await expanded.get('[data-testid="getting-started-close"]').trigger('click')
    expect(expanded.emitted('dismiss')).toHaveLength(1)
    expect(expanded.emitted('collapse')).toBeUndefined()
    expanded.unmount()

    const collapsed = mountCard({ hasGames: true })
    expect(collapsed.find('[data-testid="getting-started-close"]').exists()).toBe(true)
    await collapsed.get('[data-testid="getting-started-close"]').trigger('click')
    expect(collapsed.emitted('dismiss')).toHaveLength(1)
    expect(collapsed.emitted('collapse')).toBeUndefined()
  })

  it('keeps the Join friends action available without fetching config', async () => {
    const wrapper = mountCard()
    await wrapper.get('[data-testid="getting-started-friends"]').trigger('click')
    expect(wrapper.text()).toContain('邀请')
    expect(apiMock).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="getting-started-connect"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="getting-started-connect"]').text()).toBe('加入好友')
    await wrapper.get('[data-testid="getting-started-connect"]').trigger('click')
    expect(wrapper.emitted('connect')).toHaveLength(1)
    expect(apiMock).not.toHaveBeenCalled()
  })

  it.each(['zh-CN', 'en', 'ja', 'de', 'ru'] as Locale[])('has a locale-specific translated title for %s', (locale) => {
    i18n.global.locale.value = locale
    const wrapper = mountCard()
    const expected: Record<Locale, RegExp> = {
      'zh-CN': /第一回合/,
      en: /first round/,
      ja: /ラウンド/,
      de: /Runde/,
      ru: /раунда/,
    }
    expect(wrapper.get('h2').text()).toMatch(expected[locale])
    wrapper.unmount()
  })
})
