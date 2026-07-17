import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, errorMessage } from '@/api/client'
import type { AppConfig, TestResult } from '@/api/types'

export type SecretKey =
  | 'api_key' | 'embedding_api_key' | 'fallback1_api_key' | 'fallback2_api_key'
  | 'bot_token' | 'napcat_token' | 'proxy_url'
export type SecretInput = Partial<Record<SecretKey, string>>

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

  function collectSecrets(keys: SecretKey[]): Record<string, string> {
    const out: Record<string, string> = {}
    for (const k of keys) {
      const v = secrets.value[k]?.trim()
      if (v) out[k] = v
    }
    return out
  }

  async function saveSection(keys: string[], secretKeys: SecretKey[] = []) {
    const payload: Record<string, unknown> = {}
    for (const k of keys) if (k in config.value) payload[k] = getConfigField(k as keyof AppConfig)
    Object.assign(payload, collectSecrets(secretKeys))
    await api('/config', { method: 'POST', body: JSON.stringify(payload) })
    for (const k of secretKeys) secrets.value[k] = ''
    await load()
  }

  async function saveAccessPassword(password: string) {
    await api('/config', { method: 'POST', body: JSON.stringify({ access_token: password }) })
    localStorage.setItem('trpg_access_token', password)
    await load()
  }

  async function clearProxy() {
    await api('/config', { method: 'POST', body: JSON.stringify({ proxy_enabled: false, proxy_url: '' }) })
    await load()
  }

  async function test(kind: 'model' | 'embedding' | 'proxy'): Promise<TestResult> {
    const path = kind === 'proxy' ? '/test-proxy' : kind === 'embedding' ? '/test-embedding' : '/test-connection'
    const body: Record<string, unknown> = {
      ...(config.value as Record<string, unknown>),
    }
    for (const key of ['api_key', 'embedding_api_key', 'fallback1_api_key', 'fallback2_api_key']) {
      delete body[key]
    }
    Object.assign(body, collectSecrets(['api_key', 'embedding_api_key', 'fallback1_api_key', 'fallback2_api_key', 'proxy_url']))
    if (kind === 'embedding') {
      // The backend embedding test reads body.base_url/model/api_key for legacy compatibility.
      // Map the embedding_* config fields and pass only plaintext secrets, not SecretField objects.
      body.base_url = String(getConfigField('embedding_base_url') ?? '').trim()
      body.model = String(getConfigField('embedding_model') ?? '').trim()
      body.api_key = secrets.value.embedding_api_key?.trim() || secrets.value.api_key?.trim()
    }
    return api<TestResult>(path, { method: 'POST', body: JSON.stringify(body) })
  }

  return { config, secrets, loading, error, load, saveSection, saveAccessPassword, clearProxy, test, setConfigField }
})
