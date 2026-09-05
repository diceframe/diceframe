import type { AppConfig } from '@/api/types'

/** Model routing needs a saved provider and model; local providers may have no key. */
export function isLlmConfigReady(config: Partial<AppConfig>): boolean {
  const providerRef = String(config.llm_provider_ref || '').trim()
  const provider = (config.ai_providers || []).find(item => item.id === providerRef)
  return Boolean(
    providerRef
    && provider
    && String(provider.base_url || '').trim()
    && String(config.model || '').trim(),
  )
}
