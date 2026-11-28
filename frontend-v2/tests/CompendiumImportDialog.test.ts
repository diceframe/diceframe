import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import CompendiumImportDialog from '../src/features/modules/CompendiumImportDialog.vue'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import type {
  ContentSyncCommitResponse,
  ContentSyncPlanItem,
  ContentSyncPreviewResponse,
} from '../src/api/contentSync'

function planItem(overrides: Partial<ContentSyncPlanItem> = {}): ContentSyncPlanItem {
  return {
    client_ref: 'pack.journal.j1',
    kind: 'lorebook',
    draft_digest: 'sha256:draft',
    action: 'create',
    existing: null,
    allowed: [],
    reason: '',
    ...overrides,
  }
}

function existingItem(overrides: Partial<ContentSyncPlanItem> = {}): ContentSyncPlanItem {
  return planItem({
    action: 'update',
    existing: {
      canonical_id: 'import:device:abc',
      state_token: '3',
      server_modified: false,
      name: 'Greymoor Journal',
      matched_by: 'identity',
      entries_add: 1,
      entries_update: 2,
      entries_remove: 0,
      other_matches: [],
    },
    allowed: ['update', 'duplicate', 'skip'],
    ...overrides,
  })
}

function previewResponse(items: ContentSyncPlanItem[]): ContentSyncPreviewResponse {
  return { ok: true, plan_digest: 'sha256:plan', items }
}

function commitResponse(overrides: Partial<ContentSyncCommitResponse> = {}): ContentSyncCommitResponse {
  return {
    ok: true,
    items: [{ client_ref: 'pack.journal.j1', kind: 'lorebook', status: 'created', canonical_id: 'import:device:abc', state_token: '1', entries_removed: 0 }],
    ...overrides,
  }
}

function open(apiMock: ReturnType<typeof vi.fn>) {
  vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
  return mount(CompendiumImportDialog, {
    global: { plugins: [i18n], stubs: { Modal: { template: '<div><slot /></div>' } } },
  })
}

async function chooseFile(wrapper: ReturnType<typeof open>, content: string, name = 'journal_pack.json', size?: number) {
  const input = wrapper.get('[data-testid="compendium-import-file"]')
  const file = new File([content], name, { type: 'application/json' })
  if (size !== undefined) Object.defineProperty(file, 'size', { value: size })
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

async function runPreview(wrapper: ReturnType<typeof open>) {
  await wrapper.get('[data-testid="compendium-import-preview"]').trigger('click')
  await flushPromises()
}

function bodies(apiMock: ReturnType<typeof vi.fn>) {
  return (apiMock.mock.calls as unknown as [string, RequestInit][]).map(([path, init]) => ({
    path,
    body: JSON.parse(init.body as string),
  }))
}

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
  localStorage.clear()
})

describe('CompendiumImportDialog', () => {
  it('previews the source text as one foundry-compendium lorebook item', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([planItem({ warnings: [{ code: 'FOUNDRY_COMPENDIUM_UNSUPPORTED', message: "RollTable 'Loot' was kept in the import report only" }] })]))
      : Promise.reject(new Error(`unexpected ${path}`)))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[{"_id":"j1","name":"Greymoor","pages":[]}]')
    await runPreview(wrapper)

    const preview = bodies(apiMock).find(entry => entry.path === '/content/import/preview')
    expect(preview).toBeTruthy()
    expect(preview!.body.source.kind).toBe('device')
    expect(preview!.body.source.id).toMatch(/^web-/)
    expect(preview!.body.items).toHaveLength(1)
    expect(preview!.body.items[0]).toMatchObject({ kind: 'lorebook', format: 'foundry-compendium', document: '[{"_id":"j1","name":"Greymoor","pages":[]}]' })
    expect(preview!.body.items[0].client_ref).toBe('journal_pack')
    expect(wrapper.find('[data-testid="compendium-import-plan"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="compendium-warning"]').text()).toContain('Unmapped document')
    expect(wrapper.get('[data-testid="compendium-warning"]').text()).toContain('RollTable')
  })

  it('creates a new Book without a decision and shows the receipt', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([planItem()]))
      : Promise.resolve(commitResponse()))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[]')
    await runPreview(wrapper)

    expect(wrapper.get('[data-testid="compendium-import-commit"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="compendium-import-commit"]').trigger('click')
    await flushPromises()

    const commit = bodies(apiMock).find(entry => entry.path === '/content/import/commit')
    expect(commit!.body.plan_digest).toBe('sha256:plan')
    expect(commit!.body.decisions).toEqual({})
    expect(commit!.body.items[0].format).toBe('foundry-compendium')
    expect(wrapper.get('[data-testid="compendium-receipt-ok"]').text()).toContain('content library')
    expect(wrapper.get('[data-testid="compendium-receipt-item"]').text()).toContain('Created')
    expect(wrapper.emitted('imported')).toHaveLength(1)
  })

  it('requires an explicit decision for an existing Book before committing', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([existingItem()]))
      : Promise.resolve(commitResponse({ items: [{ client_ref: 'pack.journal.j1', kind: 'lorebook', status: 'updated', canonical_id: 'import:device:abc', state_token: '4', entries_removed: 0 }] })))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[]')
    await runPreview(wrapper)

    expect(wrapper.find('[data-testid="compendium-decision-required"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="compendium-import-commit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="compendium-plan-item"]').text()).toContain('Greymoor Journal')

    await wrapper.get('[data-testid="compendium-decision"]').setValue('update')
    expect(wrapper.get('[data-testid="compendium-import-commit"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="compendium-import-commit"]').trigger('click')
    await flushPromises()

    const commit = bodies(apiMock).find(entry => entry.path === '/content/import/commit')
    expect(commit!.body.decisions).toEqual({ 'pack.journal.j1': 'update' })
    expect(wrapper.get('[data-testid="compendium-receipt-item"]').text()).toContain('Updated')
  })

  it('sends duplicate and skip decisions verbatim', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([existingItem()]))
      : Promise.resolve(commitResponse({ items: [{ client_ref: 'pack.journal.j1', kind: 'lorebook', status: 'duplicated', canonical_id: 'import:device:new', state_token: '1', entries_removed: 0 }] })))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[]')
    await runPreview(wrapper)
    await wrapper.get('[data-testid="compendium-decision"]').setValue('duplicate')
    await wrapper.get('[data-testid="compendium-import-commit"]').trigger('click')
    await flushPromises()
    expect(bodies(apiMock).find(entry => entry.path === '/content/import/commit')!.body.decisions).toEqual({ 'pack.journal.j1': 'duplicate' })
    expect(wrapper.get('[data-testid="compendium-receipt-item"]').text()).toContain('Duplicated')
  })

  it('prompts a re-preview on PLAN_STALE and recovers', async () => {
    let stale = true
    const apiMock = vi.fn((path: string) => {
      if (path.endsWith('/preview')) return Promise.resolve(previewResponse([planItem()]))
      return stale
        ? Promise.reject(new ApiError('stale plan', 409, 'PLAN_STALE'))
        : Promise.resolve(commitResponse())
    })
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[]')
    await runPreview(wrapper)
    await wrapper.get('[data-testid="compendium-import-commit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="compendium-import-stale"]').text()).toContain('Preview expired')
    expect(wrapper.find('[data-testid="compendium-import-receipt"]').exists()).toBe(false)

    stale = false
    await wrapper.get('[data-testid="compendium-import-repreview"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="compendium-import-stale"]').exists()).toBe(false)
    await wrapper.get('[data-testid="compendium-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="compendium-import-receipt"]').exists()).toBe(true)
  })

  it('surfaces a localized malformed-source error', async () => {
    const apiMock = vi.fn(() => Promise.reject(new ApiError('raw backend text', 400, 'FOUNDRY_COMPENDIUM_MALFORMED')))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, 'not json')
    await runPreview(wrapper)
    expect(wrapper.get('[data-testid="compendium-import-error"]').text()).toContain('compendium source is malformed')
    expect(wrapper.find('[data-testid="compendium-import-plan"]').exists()).toBe(false)
  })

  it('shows a partial-failure receipt when an item errors', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([planItem()]))
      : Promise.resolve(commitResponse({ ok: false, items: [{ client_ref: 'pack.journal.j1', status: 'error', error_code: 'IMPORT_FAILED', error: 'the server could not import this item' }] })))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[]')
    await runPreview(wrapper)
    await wrapper.get('[data-testid="compendium-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="compendium-receipt-partial"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="compendium-receipt-item"]').text()).toContain('Failed')
  })

  it('rejects an oversized file locally without calling the API', async () => {
    const apiMock = vi.fn()
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[]', 'big.json', 9 * 1024 * 1024)
    expect(wrapper.get('[data-testid="compendium-import-error"]').text()).toContain('8 MiB')
    expect(apiMock).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="compendium-import-preview"]').attributes('disabled')).toBeDefined()
  })

  it('blocks preview while the import identity is invalid', async () => {
    const apiMock = vi.fn()
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[]')
    await wrapper.get('[data-testid="compendium-import-ref"]').setValue('bad ref!')
    expect(wrapper.find('[data-testid="compendium-ref-invalid"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="compendium-import-preview"]').attributes('disabled')).toBeDefined()
    expect(apiMock).not.toHaveBeenCalled()
  })

  it('renders Chinese copy when the locale is zh-CN', async () => {
    i18n.global.locale.value = 'zh-CN'
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([planItem()]))
      : Promise.resolve(commitResponse()))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[]')
    await runPreview(wrapper)
    expect(wrapper.text()).toContain('导入计划')
    expect(wrapper.get('[data-testid="compendium-import-commit"]').text()).toContain('提交导入')
  })
})