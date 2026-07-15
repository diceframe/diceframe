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
      const v = secrets.value[k]
      if (v) out[k] = v
    }
    return out
  }

  async function saveSection(keys: string[], secretKeys: SecretKey[] = []) {
    const payload: Record<string, unknown> = {}
    for (const k of keys) if (k in config.value) payload[k] = (config.value as Record<string, unknown>)[k]
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
      ...collectSecrets(['api_key', 'embedding_api_key', 'fallback1_api_key', 'fallback2_api_key', 'proxy_url']),
    }
    if (kind === 'embedding') {
      // 后端 api_test_embedding 读 body.base_url/model/api_key（与 legacy 约定一致）；
      // config 里 embedding 字段名是 embedding_base_url/embedding_model，需映射，
      // 且 config.api_key 是 SecretField 对象（{configured,masked}）不能直接传，只传 secrets 里的明文 key。
      body.base_url = (config.value as Record<string, unknown>).embedding_base_url ?? ''
      body.model = (config.value as Record<string, unknown>).embedding_model
      body.api_key = secrets.value.embedding_api_key || secrets.value.api_key
    }
    return api<TestResult>(path, { method: 'POST', body: JSON.stringify(body) })
  }

  return { config, secrets, loading, error, load, saveSection, saveAccessPassword, clearProxy, test }
})
