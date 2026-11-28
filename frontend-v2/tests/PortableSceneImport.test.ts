import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import PortableImportDialog from '../src/features/tactical-scene/PortableImportDialog.vue'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import type { PortableSceneImportResponse, PortableScenePreviewResponse, TacticalSceneDocument } from '../src/api/tacticalScene'

const definition = { spec: 'tactical-scene-v1', tactical_scene: { canonical_id: 'scene-a', display_name: 'Plaza' } }

function previewResponse(overrides: Partial<PortableScenePreviewResponse> = {}): PortableScenePreviewResponse {
  return { ok: true, action: 'create', scene_id: 'scene-a', revision: 2, name_collision: false, ...overrides }
}

function importResponse(overrides: Partial<PortableSceneImportResponse> = {}): PortableSceneImportResponse {
  return {
    ok: true,
    created: true,
    scene_id: 'scene-a',
    revision: 3,
    active_scene_ref: null,
    scene: { canonical_id: 'scene-a', display_name: 'Plaza' } as unknown as TacticalSceneDocument,
    operation: 'tactical_scene_create',
    ...overrides,
  }
}

function open(apiMock: ReturnType<typeof vi.fn>) {
  vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
  return mount(PortableImportDialog, {
    props: { gameKey: 'one' },
    global: { plugins: [i18n], stubs: { Modal: { template: '<div><slot /></div>' } } },
  })
}

async function chooseFile(wrapper: ReturnType<typeof open>, content: string, name = 'scene.json') {
  const input = wrapper.get('[data-testid="portable-import-file"]')
  Object.defineProperty(input.element, 'files', { value: [new File([content], name, { type: 'application/json' })], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

async function runPreview(wrapper: ReturnType<typeof open>) {
  await wrapper.get('[data-testid="portable-import-preview"]').trigger('click')
  await flushPromises()
}

function bodies(apiMock: ReturnType<typeof vi.fn>) {
  return (apiMock.mock.calls as unknown as [string, RequestInit][]).map(([path, init]) => ({ path, body: JSON.parse(init.body as string) }))
}

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
  localStorage.clear()
})

describe('PortableImportDialog', () => {
  it('previews the definition and commits with confirm and expected_revision', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/import/preview')
      ? Promise.resolve(previewResponse())
      : Promise.resolve(importResponse()))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, JSON.stringify(definition))
    await runPreview(wrapper)

    const preview = bodies(apiMock).find(entry => entry.path === '/games/one/tactical-scenes/import/preview')
    expect(preview!.body.definition).toEqual(definition)
    expect(wrapper.get('[data-testid="portable-preview-action"]').text()).toContain('create a new scene')
    expect(wrapper.get('[data-testid="portable-import-commit"]').attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-testid="portable-import-commit"]').trigger('click')
    await flushPromises()
    const commit = bodies(apiMock).find(entry => entry.path === '/games/one/tactical-scenes/import')
    expect(commit!.body).toMatchObject({ definition, confirm: true, expected_revision: 2 })
    expect(commit!.body.confirm_name_collision).toBeUndefined()
    expect(wrapper.get('[data-testid="portable-receipt-created"]').text()).toContain('Scene created')
    expect(wrapper.emitted('imported')).toHaveLength(1)
  })

  it('shows the noop receipt when the same scene already exists', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/import/preview')
      ? Promise.resolve(previewResponse({ action: 'noop', scene: { canonical_id: 'scene-a' } as unknown as TacticalSceneDocument }))
      : Promise.resolve(importResponse({ created: false, operation: undefined })))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, JSON.stringify(definition))
    await runPreview(wrapper)
    expect(wrapper.get('[data-testid="portable-preview-action"]').text()).toContain('nothing to write')
    await wrapper.get('[data-testid="portable-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="portable-receipt-noop"]').text()).toContain('nothing was written again')
  })

  it('requires confirm_name_collision for a same-named local scene', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/import/preview')
      ? Promise.resolve(previewResponse({ name_collision: true }))
      : Promise.resolve(importResponse()))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, JSON.stringify(definition))
    await runPreview(wrapper)

    expect(wrapper.find('[data-testid="portable-name-collision"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="portable-import-commit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="portable-confirm-name"]').setValue(true)
    expect(wrapper.get('[data-testid="portable-import-commit"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="portable-import-commit"]').trigger('click')
    await flushPromises()
    const commit = bodies(apiMock).find(entry => entry.path === '/games/one/tactical-scenes/import')
    expect(commit!.body.confirm_name_collision).toBe(true)
  })

  it('surfaces a name_conflict refusal and offers the confirmation', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/import/preview')
      ? Promise.resolve(previewResponse())
      : Promise.reject(new ApiError('name conflict', 409, 'scene_import_name_conflict')))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, JSON.stringify(definition))
    await runPreview(wrapper)
    await wrapper.get('[data-testid="portable-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="portable-import-error"]').text()).toContain('already has this name')
    expect(wrapper.find('[data-testid="portable-name-collision"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="portable-import-receipt"]').exists()).toBe(false)
  })

  it('localizes an identity conflict without offering overwrite', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/import/preview')
      ? Promise.resolve(previewResponse())
      : Promise.reject(new ApiError('conflict', 409, 'scene_import_conflict')))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, JSON.stringify(definition))
    await runPreview(wrapper)
    await wrapper.get('[data-testid="portable-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="portable-import-error"]').text()).toContain('never overwrite')
    expect(wrapper.find('[data-testid="portable-import-receipt"]').exists()).toBe(false)
  })

  it('prompts a re-preview on a stale slot revision and recovers', async () => {
    let stale = true
    const apiMock = vi.fn((path: string) => {
      if (path.endsWith('/import/preview')) return Promise.resolve(previewResponse())
      return stale
        ? Promise.reject(new ApiError('stale', 409, 'tactical_scene_stale_revision'))
        : Promise.resolve(importResponse())
    })
    const wrapper = open(apiMock)
    await chooseFile(wrapper, JSON.stringify(definition))
    await runPreview(wrapper)
    await wrapper.get('[data-testid="portable-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="portable-import-stale"]').text()).toContain('Preview expired')

    stale = false
    await wrapper.get('[data-testid="portable-import-repreview"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="portable-import-stale"]').exists()).toBe(false)
    await wrapper.get('[data-testid="portable-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="portable-import-receipt"]').exists()).toBe(true)
  })

  it('rejects a file that is not a JSON object', async () => {
    const apiMock = vi.fn()
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '[1,2,3]')
    expect(wrapper.get('[data-testid="portable-import-error"]').text()).toContain('not a valid JSON object')
    expect(apiMock).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="portable-import-preview"]').attributes('disabled')).toBeDefined()
  })

  it('renders Chinese copy when the locale is zh-CN', async () => {
    i18n.global.locale.value = 'zh-CN'
    const apiMock = vi.fn((path: string) => path.endsWith('/import/preview')
      ? Promise.resolve(previewResponse())
      : Promise.resolve(importResponse()))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, JSON.stringify(definition))
    await runPreview(wrapper)
    expect(wrapper.text()).toContain('导入预览')
    expect(wrapper.get('[data-testid="portable-import-commit"]').text()).toContain('确认导入')
  })
})