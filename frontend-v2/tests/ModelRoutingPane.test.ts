import { flushPromises, mount, type DOMWrapper } from '@vue/test-utils'
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

function setup(config: Partial<AppConfig>, supported = true, dirty = false) {
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
      dirty,
      onSave: () => { void settings.saveModelRouting() },
    },
    global: {
      plugins: [i18n],
      stubs: {
        HelpButton: true,
        GenerationSettings: true,
        TestResultCard: true,
        TtsVoiceProfiles: true,
        teleport: true,
      },
    },
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

it('owns speech engine parameters and tests inside the TTS and ASR cards', async () => {
  const { wrapper } = setup({
    ai_providers: [{ id: 'local', name: 'Local', base_url: 'http://localhost:8000/v1', api_format: 'openai', models: ['tts-1', 'whisper-1'] }],
    tts_provider: 'openai-compatible', tts_provider_ref: 'local', tts_model: 'tts-1',
    tts_audio_format: 'mp3', tts_cache_mb: 256,
    asr_provider: 'openai-compatible', asr_provider_ref: 'local', asr_model: 'whisper-1', asr_timeout_seconds: 60,
  })
  const tts = wrapper.get('#settings-tts-model')
  const asr = wrapper.get('#settings-asr')
  expect(tts.find('tts-voice-profiles-stub').exists()).toBe(true)
  await tts.findAll('select').find(select => select.find('option[value="opus"]').exists())!.setValue('opus')
  await tts.get('.model-role-actions button').trigger('click')
  await asr.get('.model-role-actions button').trigger('click')
  expect(wrapper.emitted('test-tts')).toHaveLength(1)
  expect(wrapper.emitted('test-asr')).toHaveLength(1)

  await wrapper.get('.model-routing-save').trigger('click')
  await flushPromises()
  const post = mocks.api.mock.calls.find(([, options]) => options?.method === 'POST')
  expect(JSON.parse(post![1].body)).toMatchObject({ tts_audio_format: 'opus', tts_cache_mb: 256, asr_timeout_seconds: 60 })
  wrapper.unmount()
})

it('keeps engine-only TTS fields hidden for the browser voice', () => {
  const { wrapper } = setup({ ai_providers: [], tts_provider: 'browser', asr_provider: 'disabled' })
  expect(wrapper.get('#settings-tts-model').find('.model-role-actions').exists()).toBe(false)
  expect(wrapper.get('#settings-asr').find('.model-role-actions').exists()).toBe(false)
  wrapper.unmount()
})

it('keeps routing unavailable when the backend does not support provider configuration', () => {
  const { wrapper } = setup({ ai_providers: [] }, false)
  expect(wrapper.findAll('select')).toHaveLength(0)
  expect(wrapper.get('.model-routing-save').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})

it('makes unsaved model changes visible next to the save action', () => {
  const { wrapper } = setup({ ai_providers: [] }, true, true)
  expect(wrapper.get('.model-routing-save-state').text()).toBe('有未保存更改')
  expect(wrapper.get('.model-routing-unsaved-hint').text()).toContain('尚未生效')
  wrapper.unmount()
})

const openAiProvider = { id: 'openai', name: 'OpenAI', base_url: 'http://localhost:8000/v1', api_format: 'openai', models: ['chat'] }
const anthropicProvider = { id: 'claude', name: 'Claude', base_url: 'https://api.anthropic.com', api_format: 'anthropic', models: ['claude'] }
const optionValues = (field: Pick<DOMWrapper<Element>, 'findAll'>) => field.findAll('option').map(option => option.attributes('value'))

it('saves each chat route’s reasoning effort with model routing', async () => {
  const { wrapper, store } = setup({
    ai_providers: [openAiProvider],
    llm_provider_ref: 'openai', model: 'chat',
    fallback1_enabled: true, fallback1_provider_ref: 'openai', fallback1_model: 'chat',
    check_model_enabled: true, check_provider_ref: 'openai', check_model: 'chat',
  })
  const efforts = wrapper.findAll('#settings-reasoning-effort, .model-fallback-slot .reasoning-effort-field, #settings-check-model .reasoning-effort-field')
  expect(efforts).toHaveLength(3)
  expect(efforts.every(field => (field.get('select').element as HTMLSelectElement).disabled === false)).toBe(true)
  await efforts[0]!.get('select').setValue('high')
  await efforts[1]!.get('select').setValue('none')
  await efforts[2]!.get('select').setValue('max')
  await wrapper.get('.model-routing-save').trigger('click')
  await flushPromises()

  const post = mocks.api.mock.calls.find(([, options]) => options?.method === 'POST')
  expect(JSON.parse(post![1].body)).toMatchObject({
    model_reasoning_effort: 'high', fallback1_reasoning_effort: 'none', check_model_reasoning_effort: 'max',
  })
  expect(store.config.model_reasoning_effort).toBe('high')
  wrapper.unmount()
})

it('offers only the levels the provider format can express and resets the level when the provider changes', async () => {
  const { wrapper, store } = setup({
    ai_providers: [openAiProvider, anthropicProvider],
    llm_provider_ref: 'openai', model: 'chat', model_reasoning_effort: 'high',
  })
  const main = wrapper.get('#settings-reasoning-effort')
  expect(optionValues(main)).toEqual(['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'custom'])
  expect((main.get('select').element as HTMLSelectElement).value).toBe('high')

  await wrapper.get('.model-role-card-main > label select').setValue('claude')
  expect(store.config.model_reasoning_effort).toBe('')
  expect(optionValues(main)).toEqual(['', 'low', 'medium', 'high', 'xhigh', 'max', 'custom'])
  wrapper.unmount()
})

it('keeps reasoning effort disabled until the route has a provider', () => {
  const { wrapper } = setup({ ai_providers: [openAiProvider], llm_provider_ref: '', model: '' })
  const main = wrapper.get('#settings-reasoning-effort')
  expect((main.get('select').element as HTMLSelectElement).disabled).toBe(true)
  expect(main.text()).toContain('选择服务商后可设置')
  wrapper.unmount()
})

it('saves a provider-specific identifier entered under Custom', async () => {
  const { wrapper, store } = setup({
    ai_providers: [openAiProvider], llm_provider_ref: 'openai', model: 'chat', model_reasoning_effort: 'high',
  })
  const main = wrapper.get('#settings-reasoning-effort')
  await main.get('select').setValue('custom')
  expect(store.config.model_reasoning_effort).toBe('')
  expect(main.find('input').exists()).toBe(true)

  await main.get('input').setValue('  provider-ultra  ')
  expect(store.config.model_reasoning_effort).toBe('provider-ultra')
  await wrapper.get('.model-routing-save').trigger('click')
  await flushPromises()

  const post = mocks.api.mock.calls.find(([, options]) => options?.method === 'POST')
  expect(JSON.parse(post![1].body)).toMatchObject({ model_reasoning_effort: 'provider-ultra' })
  wrapper.unmount()
})

it('shows a stored identifier as Custom and leaves custom mode when the route is reset', async () => {
  const { wrapper, store } = setup({
    ai_providers: [openAiProvider, anthropicProvider],
    llm_provider_ref: 'openai', model: 'chat', model_reasoning_effort: 'provider-ultra',
  })
  const main = wrapper.get('#settings-reasoning-effort')
  expect((main.get('select').element as HTMLSelectElement).value).toBe('custom')
  expect((main.get('input').element as HTMLInputElement).value).toBe('provider-ultra')

  await wrapper.get('.model-role-card-main > label select').setValue('claude')
  expect(store.config.model_reasoning_effort).toBe('')
  expect((main.get('select').element as HTMLSelectElement).value).toBe('')
  expect(main.find('input').exists()).toBe(false)
  wrapper.unmount()
})

it('shows visible feedback when a manual memory-index rebuild finishes', async () => {
  const config = {
    ai_providers: [],
    embedding_enabled: true,
    embedding_source: 'external',
    embedding_status: { state: 'ready', pending: 2, total_active: 2, ready_current_profile: 0 },
  } as Partial<AppConfig>
  const { wrapper } = setup(config)
  let rebuildRequested = false
  mocks.api.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === '/embedding/rebuild') {
      rebuildRequested = true
      return { ok: true, state: 'rebuilding' }
    }
    if (path === '/config' && (!options || !options.method || options.method === 'GET')) {
      return {
        ...config,
        embedding_status: rebuildRequested
          ? { state: 'ready', pending: 0, total_active: 2, ready_current_profile: 2 }
          : config.embedding_status,
      }
    }
    return {}
  })

  await wrapper.get('[data-testid="embedding-index-status"] button').trigger('click')
  await flushPromises()

  expect(wrapper.get('[data-testid="embedding-rebuild-feedback"]').text()).toContain('重建完成')
  wrapper.unmount()
})

it('keeps image generation basics visible and exposes advanced fields on demand', async () => {
  const { wrapper } = setup({
    ai_providers: [{ id: 'image', name: 'Image', base_url: 'http://localhost:8000/v1', api_format: 'openai', models: ['image-1'] }],
    imagegen_enabled: true,
    imagegen_auto_scene: true,
    imagegen_manual_scene: false,
    imagegen_provider_ref: 'image',
    imagegen_model: 'image-1',
  })

  expect(wrapper.text()).toContain('自动生成场景图')
  expect(wrapper.text()).toContain('手动生成场景图')
  expect(wrapper.text()).toContain('统一风格前缀')
  expect(wrapper.text()).toContain('高级图像设置')
  expect(wrapper.text()).toContain('手动提示词模板')
  expect(wrapper.find('textarea').exists()).toBe(true)
  const advancedSettings = wrapper.get('.imagegen-advanced-settings')
  expect(advancedSettings.text()).toContain('高级图像设置')
  expect(advancedSettings.element).not.toHaveProperty('open', true)
  await advancedSettings.find('summary').trigger('click')
  await flushPromises()
  expect(advancedSettings.element).toHaveProperty('open', true)
  expect(wrapper.text()).toContain('留空使用默认尺寸')
  wrapper.unmount()
})

it('hides inactive automatic prompt fields while preserving their drafts', () => {
  const { wrapper, store } = setup({
    ai_providers: [],
    imagegen_enabled: true,
    imagegen_auto_use_manual_prompt: true,
    imagegen_manual_rules: 'manual rules',
    imagegen_manual_prompt: 'manual {scene}',
    imagegen_auto_rules: 'saved automatic rules',
    imagegen_auto_prompt: 'saved automatic {scene}',
  })

  expect(wrapper.text()).toContain('自动生成当前使用上方的手动规则和模板')
  expect(wrapper.text()).not.toContain('自动提示词规则')
  expect(wrapper.text()).not.toContain('自动提示词模板')
  expect(store.config.imagegen_auto_rules).toBe('saved automatic rules')
  expect(store.config.imagegen_auto_prompt).toBe('saved automatic {scene}')
  expect(wrapper.get('textarea[placeholder*="写实奇幻插画"]').attributes('placeholder')).toContain('写实奇幻插画')
  wrapper.unmount()
})

it('previews AI optimization and only updates the unsaved settings draft', async () => {
  const { wrapper, store } = setup({
    ai_providers: [],
    imagegen_enabled: true,
    imagegen_style_prefix: 'fantasy art',
    imagegen_auto_use_manual_prompt: true,
  })
  mocks.api.mockImplementation(async (path: string) => {
    if (path === '/image-prompts/optimize') {
      return { ok: true, text: 'cinematic grounded fantasy art' }
    }
    return {}
  })

  await wrapper.get('.imagegen-style-field button').trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('cinematic grounded fantasy art')
  expect(store.config.imagegen_style_prefix).toBe('fantasy art')
  const buttons = wrapper.findAll('.imagegen-optimizer-preview button')
  await buttons[buttons.length - 1].trigger('click')
  expect(store.config.imagegen_style_prefix).toBe('cinematic grounded fantasy art')
  expect(mocks.api.mock.calls.some(([path]) => path === '/config')).toBe(false)
  wrapper.unmount()
})

it('shows the MiniMax budget and warns when static configuration uses over half', () => {
  const { wrapper } = setup({
    ai_providers: [{ id: 'minimax', name: 'MiniMax', base_url: 'https://api.minimax.cn/v1', api_format: 'openai', models: ['image-01'] }],
    imagegen_enabled: true,
    imagegen_provider_ref: 'minimax',
    imagegen_model: 'image-01',
    imagegen_style_prefix: 'x'.repeat(800),
    imagegen_auto_use_manual_prompt: true,
  })

  expect(wrapper.get('.imagegen-budget').text()).toContain('1500')
  expect(wrapper.get('.imagegen-budget').classes()).toContain('warning')
  wrapper.unmount()
})
