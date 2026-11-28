import { describe, expect, it, vi } from 'vitest'
import {
  assignCatalogModelRoleWithRollback,
  CATALOG_MODEL_ROLES,
  catalogModelRoleEligible,
  isCatalogModelAssigned,
  type CatalogSavedProvider,
} from '../src/utils/providerModels'

// 模型目录快捷分配的状态一致性契约：
// 1) 只认已持久化的 provider library（UI 草稿不算数）
// 2) 所有用途复用同一份可扩展注册表
// 3) 保存失败时完整回滚该用途涉及的字段

describe('catalogModelRoleEligible', () => {
  it('declares model-configuration roles in one extensible registry', () => {
    expect(CATALOG_MODEL_ROLES.map(role => role.id)).toEqual(['main', 'check', 'embedding', 'imagegen', 'asr'])
  })

  it('accepts persisted models only for a matching role', () => {
    const saved: CatalogSavedProvider[] = [{
      id: 'provider-a',
      api_format: 'openai',
      models: ['chat-a', 'embed-a', 'image-a', 'asr-a'],
      model_capabilities: { 'embed-a': 'embedding', 'image-a': 'image', 'asr-a': 'asr' },
    }]
    expect(catalogModelRoleEligible(saved, 'provider-a', 'chat-a', 'main')).toBe(true)
    expect(catalogModelRoleEligible(saved, 'provider-a', 'embed-a', 'embedding')).toBe(true)
    expect(catalogModelRoleEligible(saved, 'provider-a', 'image-a', 'imagegen')).toBe(true)
    expect(catalogModelRoleEligible(saved, 'provider-a', 'asr-a', 'asr')).toBe(true)
    expect(catalogModelRoleEligible(saved, 'provider-a', 'chat-a', 'imagegen')).toBe(false)
  })

  it('rejects models that only exist in the unsaved draft', () => {
    // 持久化：provider A models = [model-a]；草稿新加 model-new(chat) → 不可分配
    const persisted = [{ id: 'provider-a', models: ['model-a'] }]
    expect(catalogModelRoleEligible(persisted, 'provider-a', 'model-new', 'main')).toBe(false)
  })

  it('uses the saved capability, not the draft override', () => {
    // 持久化里 model-x 是 tts，草稿刚改成 chat 但未保存 → 不可分配给主模型
    const persisted: CatalogSavedProvider[] = [
      { id: 'provider-b', models: ['model-x'], model_capabilities: { 'model-x': 'tts' } },
    ]
    expect(catalogModelRoleEligible(persisted, 'provider-b', 'model-x', 'main')).toBe(false)
  })

  it('rejects providers that are not persisted yet', () => {
    expect(catalogModelRoleEligible([], 'provider-c', 'model-c', 'main')).toBe(false)
  })

  it('keeps the existing OpenAI-only image-generation boundary', () => {
    const anthropic: CatalogSavedProvider[] = [{
      id: 'provider-a',
      api_format: 'anthropic',
      models: ['image-a'],
      model_capabilities: { 'image-a': 'image' },
    }]
    expect(catalogModelRoleEligible(anthropic, 'provider-a', 'image-a', 'imagegen')).toBe(false)
  })
})

describe('assignCatalogModelRoleWithRollback', () => {
  it('restores provider, model, and activation fields when saving fails', async () => {
    const config: Record<string, unknown> = {
      imagegen_provider_ref: 'provider-a',
      imagegen_model: 'image-a',
      imagegen_enabled: false,
    }
    const save = vi.fn(async () => false)

    const ok = await assignCatalogModelRoleWithRollback(config, 'imagegen', 'provider-b', 'image-b', save)

    expect(ok).toBe(false)
    expect(save).toHaveBeenCalledTimes(1)
    expect(config.imagegen_provider_ref).toBe('provider-a')
    expect(config.imagegen_model).toBe('image-a')
    expect(config.imagegen_enabled).toBe(false)
  })

  it('keeps the new binding and enables the assigned role when saving succeeds', async () => {
    const config: Record<string, unknown> = { embedding_enabled: false }

    const ok = await assignCatalogModelRoleWithRollback(config, 'embedding', 'provider-b', 'embed-b', async () => true)

    expect(ok).toBe(true)
    expect(config.embedding_provider_ref).toBe('provider-b')
    expect(config.embedding_model).toBe('embed-b')
    expect(config.embedding_enabled).toBe(true)
    expect(isCatalogModelAssigned(config, 'embedding', 'provider-b', 'embed-b')).toBe(true)
  })

  it('returns the replaced model’s reasoning effort to default and restores it when saving fails', async () => {
    const config: Record<string, unknown> = {
      llm_provider_ref: 'provider-a', model: 'chat-a', model_reasoning_effort: 'high',
    }

    expect(await assignCatalogModelRoleWithRollback(config, 'main', 'provider-b', 'chat-b', async () => false)).toBe(false)
    expect(config.model_reasoning_effort).toBe('high')

    expect(await assignCatalogModelRoleWithRollback(config, 'main', 'provider-b', 'chat-b', async () => true)).toBe(true)
    expect(config.llm_provider_ref).toBe('provider-b')
    expect(config.model).toBe('chat-b')
    expect(config.model_reasoning_effort).toBe('')
  })

  it('restores absent fields after a thrown save error', async () => {
    const config: Record<string, unknown> = {}

    await expect(assignCatalogModelRoleWithRollback(config, 'asr', 'provider-b', 'asr-b', async () => {
      throw new Error('save failed')
    })).rejects.toThrow('save failed')

    expect(config).toEqual({})
  })
})

// 结构守卫：所有目录快捷分配必须委托给统一 helper，不能先污染 config 再捕获旧值。
describe('assignCatalogModelRole wiring structure guard', () => {
  it('delegates every config mutation to assignCatalogModelRoleWithRollback', async () => {
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    const source = readFileSync(resolve('src/composables/useProviderModelSettings.ts'), 'utf-8')
    const start = source.indexOf('async function assignCatalogModelRole')
    expect(start).toBeGreaterThan(-1)
    const body = source.slice(start, source.indexOf('\n}', start))

    expect(body).toContain('assignCatalogModelRoleWithRollback(')
    expect(body).not.toMatch(/setModelRoleProvider\(/)
    expect(body).not.toMatch(/setStr\(/)
  })
})
