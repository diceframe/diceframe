import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import ModelRoutingPane from '../src/features/admin/settings/ModelRoutingPane.vue'
import { useProviderModelSettings } from '../src/composables/useProviderModelSettings'
import { useSettingsStore } from '../src/stores/useSettingsStore'
import type { AppConfig } from '../src/api/types'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api }
})

beforeEach(() => {
  setActivePinia(createPinia())
  mocks.api.mockReset()
  i18n.global.locale.value = 'zh-CN'
})

function setup(config: Partial<AppConfig>, supported = true) {
  const store = useSettingsStore()
  store.config = structuredClone(config)
  let persisted = structuredClone(config)
  mocks.api.mockImplementation(async (_path: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      persisted = { ...persisted, ...JSON.parse(String(options.body)) }
      return {}
    }
    return structuredClone(persisted)
  })
  const settings = useProviderModelSettings({
    store,
    t: key => key,
    toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  })
  const wrapper = mount(ModelRoutingPane, {
    props: {
      supported, saving: false, embeddingTesting: false, embeddingResult: null,
      onSave: () => { void settings.saveModelRouting() },
    },
    global: { plugins: [i18n], stubs: { HelpButton: true, TestResultCard: true } },
  })
  return { wrapper, store }
}

it('allows Edge TTS with an empty provider catalog and persists its default voice', async () => {
  const { wrapper, store } = setup({
    ai_providers: [], tts_provider: 'browser', tts_provider_ref: '',
    tts_default_voice: 'alloy', asr_provider: 'disabled',
  })
  const ttsMode = wrapper.findAll('select').find(select => select.find('option[value="edge-tts"]').exists())
  expect(ttsMode).toBeDefined()
  await ttsMode!.setValue('edge-tts')
  await wrapper.get('.model-routing-save').trigger('click')
  await flushPromises()

  const post = mocks.api.mock.calls.find(([, options]) => options?.method === 'POST')
  expect(JSON.parse(post![1].body)).toMatchObject({
    tts_provider: 'edge-tts', tts_provider_ref: '', tts_default_voice: 'zh-CN-XiaoxiaoNeural',
  })
  expect(store.config.tts_provider).toBe('edge-tts')
  expect(store.config.tts_default_voice).toBe('zh-CN-XiaoxiaoNeural')
  expect(store.config.ai_providers).toEqual([])
  wrapper.unmount()
})

it('persists the OpenAI default voice after switching from Edge TTS', async () => {
  const { wrapper, store } = setup({
    ai_providers: [{ id: 'local', name: 'Local', base_url: 'http://localhost:8000/v1', api_format: 'openai', models: ['tts-1'] }],
    tts_provider: 'edge-tts', tts_provider_ref: 'local', tts_model: 'tts-1',
    tts_default_voice: 'zh-CN-XiaoxiaoNeural',
  })
  const ttsMode = wrapper.findAll('select').find(select => select.find('option[value="edge-tts"]').exists())!
  await ttsMode.setValue('openai-compatible')
  await wrapper.get('.model-routing-save').trigger('click')
  await flushPromises()

  const post = mocks.api.mock.calls.find(([, options]) => options?.method === 'POST')
  expect(JSON.parse(post![1].body)).toMatchObject({
    tts_provider: 'openai-compatible', tts_provider_ref: 'local', tts_default_voice: 'alloy',
  })
  expect(store.config.tts_default_voice).toBe('alloy')
  wrapper.unmount()
})

it('keeps routing unavailable when the backend does not support provider configuration', () => {
  const { wrapper } = setup({ ai_providers: [] }, false)
  expect(wrapper.findAll('select')).toHaveLength(0)
  expect(wrapper.get('.model-routing-save').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
