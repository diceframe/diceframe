import { afterEach, describe, expect, it, vi } from 'vitest'
import * as client from '../src/api/client'
import {
  FOUNDRY_CONVERSION_LEVELS,
  FOUNDRY_IMPORT_ACTIONS,
  FOUNDRY_IMPORT_ERROR_CODES,
  FOUNDRY_IMPORT_MAX_BYTES,
  commitFoundrySceneImport,
  previewFoundrySceneImport,
  type FoundryImportPlan,
} from '../src/api/tacticalScene'
import { messages } from '../src/i18n'

afterEach(() => { vi.restoreAllMocks() })

const plan: FoundryImportPlan = {
  schema_version: 1,
  adapter: 'compat.foundry.scene_adapter@1',
  source_version: 'v14',
  base_revision: 0,
  items: [],
  scrubbed_fields: [],
  plan_digest: 'sha256:abc',
}

describe('foundry import API client', () => {
  it('posts preview with the scene payload and declared source version', async () => {
    const response = { ok: true, plan_digest: 'sha256:abc', plan, contains_script: false, level: 'convertible' }
    const apiMock = vi.fn().mockResolvedValue(response)
    vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
    const scene = { _id: 'x', name: 'Plaza' }

    await expect(previewFoundrySceneImport('game one', scene, 'v14')).resolves.toBe(response)
    expect(apiMock).toHaveBeenCalledWith('/games/game%20one/foundry/scenes/preview', {
      method: 'POST',
      body: JSON.stringify({ scene, source_version: 'v14' }),
    })
  })

  it('posts commit with plan, digest, per-item policies and operation id', async () => {
    const response = { ok: true, idempotent: false, operation_id: 'import-op', plan_digest: 'sha256:abc', revision: 1, receipt: { operation_id: 'import-op', plan_digest: 'sha256:abc', source_version: 'v14', revision: 1, items: [] } }
    const apiMock = vi.fn().mockResolvedValue(response)
    vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
    const policies = { 'scene-a': { action: 'update' as const, expected_revision: 3 } }

    await expect(commitFoundrySceneImport('game one', plan, 'sha256:abc', policies, 'import-op')).resolves.toBe(response)
    expect(apiMock).toHaveBeenCalledWith('/games/game%20one/foundry/scenes/commit', {
      method: 'POST',
      body: JSON.stringify({ plan, plan_digest: 'sha256:abc', policies, operation_id: 'import-op' }),
    })
  })

  it('declares the eight degradation classes in severity order', () => {
    expect(FOUNDRY_CONVERSION_LEVELS).toEqual([
      'convertible',
      'saved_not_runnable',
      'degraded_display',
      'missing_external_ref',
      'missing_module_dependency',
      'unsupported_ruleset',
      'unknown_future_structure',
      'contains_script',
    ])
  })

  it('declares the four commit actions and the 16 MiB payload limit', () => {
    expect(FOUNDRY_IMPORT_ACTIONS).toEqual(['create', 'update', 'duplicate', 'skip'])
    expect(FOUNDRY_IMPORT_MAX_BYTES).toBe(16 * 1024 * 1024)
  })

  it('registers localized copy for every foundry error code in both core locales', () => {
    const en = messages.en.apiErrors as Record<string, string>
    const zh = messages['zh-CN'].apiErrors as Record<string, string>
    for (const code of FOUNDRY_IMPORT_ERROR_CODES) {
      expect(en[code], `en apiErrors.${code}`).toBeTruthy()
      expect(zh[code], `zh-CN apiErrors.${code}`).toBeTruthy()
    }
  })
})