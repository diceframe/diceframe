import { describe, expect, it } from 'vitest'
import { isLlmConfigReady } from '../src/utils/modelConfiguration'

describe('isLlmConfigReady', () => {
  it('rejects legacy inline model configuration', () => {
    expect(isLlmConfigReady({
      base_url: 'https://api.example/v1',
      model: 'chat-model',
      api_key: { configured: true, masked: '***1234' },
    })).toBe(false)
  })

  it('accepts a configured provider-library model', () => {
    expect(isLlmConfigReady({
      llm_provider_ref: 'comfy',
      model: 'comfy-image',
      ai_providers: [{
        id: 'comfy',
        name: 'ComfyUI',
        base_url: 'https://comfy.example/v1',
        api_format: 'openai',
        api_key: { configured: true, masked: '***1234' },
      }],
    })).toBe(true)
  })

  it('accepts a provider without a key for local compatible services', () => {
    expect(isLlmConfigReady({
      llm_provider_ref: 'comfy',
      model: 'comfy-image',
      ai_providers: [{
        id: 'comfy',
        name: 'ComfyUI',
        base_url: 'https://comfy.example/v1',
        api_format: 'openai',
        api_key: { configured: false, masked: '' },
      }],
    })).toBe(true)
  })

  it('does not fall back to inline fields when a provider reference is active', () => {
    expect(isLlmConfigReady({
      llm_provider_ref: 'missing',
      base_url: 'https://api.example/v1',
      model: 'chat-model',
      api_key: { configured: true, masked: '***1234' },
    })).toBe(false)
  })
})
