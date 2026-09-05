import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiError, api, errorMessage, setAccessToken } from '@/api/client'
import type { AppConfig, BotTokenResponse, TestResult } from '@/api/types'

export type SecretKey = 'bot_token' | 'napcat_token' | 'proxy_url'
// 已知 secret 之外还允许动态的服务商 key（ai_provider_key_<id>）
export type SecretInput = Partial<Record<SecretKey, string>> & { [key: string]: string | undefined }

export interface AiProviderInput {
  id: string
  name: string
  base_url: string
  api_format: string
  models?: string[]
  model_capabilities?: Record<string, 'chat' | 'image' | 'embedding' | 'tts' | 'asr'>
}

export interface ProviderModelsResponse {
  ok: boolean
  models: string[]
  count?: number
  error?: string
}

export function providerSecretKey(providerId: string): string {
  return `ai_provider_key_${providerId}`
}

export const useSettingsStore = defineStore('settings', () => {
  const config = ref<Partial<AppConfig>>({})
  const secrets = ref<SecretInput>({})
  const loading = ref(false)
  const error = ref('')

  // AppConfig has a [key:string]:unknown index signature, but Partial<AppConfig>
  // still needs assertions for dynamic keys. Keep them centralized here.
  function getConfigField<T = unknown>(key: keyof AppConfig): T {
    return (config.value as Record<string, unknown>)[key] as T
  }
  function setConfigField(key: keyof AppConfig, value: unknown): void {
    ;(config.value as Record<string, unknown>)[key] = value
  }

  async function load() {
    loading.value = true
    error.value = ''
    try {
      config.value = await api<AppConfig>('/config')
    } catch (e: unknown) {
      error.value = errorMessage(e)
    } finally {
      loading.value = false
    }
  }

  function collectSecrets(keys: string[]): Record<string, string> {
    const out: Record<string, string> = {}
    for (const k of keys) {
      const v = secrets.value[k]?.trim()
      if (v) out[k] = v
    }
    return out
  }

  async function saveSection(keys: string[], secretKeys: string[] = []) {
    const payload: Record<string, unknown> = {}
    for (const k of keys) if (k in config.value) payload[k] = getConfigField(k as keyof AppConfig)
    Object.assign(payload, collectSecrets(secretKeys))
    const result = await api<{ warnings?: string[] }>('/config', { method: 'POST', body: JSON.stringify(payload) })
    for (const k of secretKeys) secrets.value[k] = ''
    await load()
    return result.warnings || []
  }

  async function saveProviders(providers: AiProviderInput[]) {
    const payload: Record<string, unknown> = {
      ai_providers: providers.map((p) => ({
        id: p.id, name: p.name, base_url: p.base_url, api_format: p.api_format,
        models: p.models || [],
        ...(Object.keys(p.model_capabilities || {}).length ? { model_capabilities: p.model_capabilities } : {}),
      })),
    }
    for (const p of providers) {
      const v = secrets.value[providerSecretKey(p.id)]?.trim()
      if (v) payload[providerSecretKey(p.id)] = v
    }
    const result = await api<{ warnings?: string[] }>('/config', { method: 'POST', body: JSON.stringify(payload) })
    const refreshed = await api<AppConfig>('/config')
    const savedProviders = Array.isArray(refreshed.ai_providers) ? refreshed.ai_providers : null
    const persisted = savedProviders && providers.every(provider => {
      const saved = savedProviders.find(item => item.id === provider.id)
      if (!saved) return false
      const expectedModels = provider.models || []
      const actualModels = saved.models || []
      const expectedCapabilities = provider.model_capabilities || {}
      const actualCapabilities = saved.model_capabilities || {}
      return expectedModels.every(model => actualModels.includes(model))
        && Object.entries(expectedCapabilities).every(([model, capability]) => actualCapabilities[model] === capability)
    })
    if (!persisted) {
      throw new ApiError(
        'The running backend does not support AI provider storage. Restart or update DiceFrame.',
        409,
        'provider_library_unsupported',
      )
    }
    config.value = refreshed
    for (const p of providers) secrets.value[providerSecretKey(p.id)] = ''
    return result.warnings || []
  }

  async function saveAccessPassword(password: string) {
    await api('/config', { method: 'POST', body: JSON.stringify({ access_token: password }) })
    setAccessToken(password)
    await load()
  }

  async function clearProxy() {
    await api('/config', { method: 'POST', body: JSON.stringify({ proxy_enabled: false, proxy_url: '' }) })
    await load()
  }

  async function botToken(action: 'reveal' | 'regenerate' = 'reveal'): Promise<BotTokenResponse> {
    const result = await api<BotTokenResponse>('/config/bot-token', {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    await load()
    return result
  }

  async function test(kind: 'model' | 'embedding' | 'proxy'): Promise<TestResult> {
    const path = kind === 'proxy' ? '/test-proxy' : kind === 'embedding' ? '/test-embedding' : '/test-connection'
    const body: Record<string, unknown> = {
      proxy_enabled: config.value.proxy_enabled,
      ...collectSecrets(['proxy_url']),
    }
    if (kind !== 'proxy') {
      body.provider_id = String(kind === 'embedding'
        ? config.value.embedding_provider_ref || '' : config.value.llm_provider_ref || '').trim()
      body.model = String(kind === 'embedding'
        ? config.value.embedding_model || '' : config.value.model || '').trim()
    }
    return api<TestResult>(path, { method: 'POST', body: JSON.stringify(body) })
  }

  async function testProvider(input: {
    providerId?: string
    baseUrl: string
    apiKey: string
    apiFormat: string
    model: string
    kind?: 'model' | 'embedding'
  }): Promise<TestResult> {
    const body: Record<string, unknown> = {
      base_url: input.baseUrl,
      api_format: input.apiFormat,
      model: input.model,
    }
    if (input.providerId) body.provider_id = input.providerId
    if (input.apiKey) body.api_key = input.apiKey
    const path = input.kind === 'embedding' ? '/test-embedding' : '/test-connection'
    return api<TestResult>(path, { method: 'POST', body: JSON.stringify(body) })
  }

  async function fetchProviderModels(input: {
    providerId?: string
    baseUrl: string
    apiKey: string
    apiFormat: string
  }): Promise<ProviderModelsResponse> {
    const body: Record<string, unknown> = {
      base_url: input.baseUrl,
      api_format: input.apiFormat,
    }
    if (input.providerId) body.provider_id = input.providerId
    if (input.apiKey) body.api_key = input.apiKey
    return api<ProviderModelsResponse>('/config/providers/models', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }

  return {
    config, secrets, loading, error, load, saveSection, saveProviders,
    saveAccessPassword, clearProxy, botToken, test, testProvider, fetchProviderModels, setConfigField,
  }
})
