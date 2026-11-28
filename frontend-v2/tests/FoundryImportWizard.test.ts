import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import ImportWizard from '../src/features/tactical-scene/ImportWizard.vue'
import ConversionReportPanel from '../src/features/tactical-scene/ConversionReportPanel.vue'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import type {
  FoundryCommitResponse,
  FoundryConversionReport,
  FoundryImportPlan,
  FoundryImportPlanItem,
  FoundryPreviewResponse,
  TacticalSceneDocument,
} from '../src/api/tacticalScene'

function report(overrides: Partial<FoundryConversionReport> = {}): FoundryConversionReport {
  return {
    source_version: 'v14',
    level: 'convertible',
    issues: [],
    preserved_fields: [],
    unknown_fields: [],
    missing_references: [],
    warnings: [],
    contains_script: false,
    ...overrides,
  }
}

function item(overrides: Partial<FoundryImportPlanItem> = {}): FoundryImportPlanItem {
  return {
    canonical_id: 'scene-a',
    source_canonical_id: 'scene-a',
    display_name: 'Plaza',
    external_ids: { foundry_v14: 'b0b0b0b0b0b0b0b0' },
    conflict: 'new',
    existing_revision: null,
    level: 'convertible',
    contains_script: false,
    report: report(),
    document: { canonical_id: 'scene-a', display_name: 'Plaza' } as unknown as TacticalSceneDocument,
    ...overrides,
  }
}

function previewResponse(items: FoundryImportPlanItem[]): FoundryPreviewResponse {
  const plan: FoundryImportPlan = {
    schema_version: 1,
    adapter: 'compat.foundry.scene_adapter@1',
    source_version: 'v14',
    base_revision: 0,
    items,
    scrubbed_fields: [],
    plan_digest: 'sha256:abc',
  }
  return {
    ok: true,
    plan_digest: 'sha256:abc',
    plan,
    contains_script: items.some(entry => entry.contains_script),
    level: items[0]?.level ?? 'convertible',
  }
}

function commitResponse(overrides: Partial<FoundryCommitResponse> = {}): FoundryCommitResponse {
  return {
    ok: true,
    idempotent: false,
    operation_id: 'import-op',
    plan_digest: 'sha256:abc',
    revision: 1,
    receipt: {
      operation_id: 'import-op',
      plan_digest: 'sha256:abc',
      source_version: 'v14',
      revision: 1,
      items: [{ canonical_id: 'scene-a', action: 'create', status: 'created' }],
    },
    ...overrides,
  }
}

function open(apiMock: ReturnType<typeof vi.fn>) {
  vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
  return mount(ImportWizard, {
    props: { gameKey: 'one' },
    global: { plugins: [i18n], stubs: { Modal: { template: '<div><slot /></div>' } } },
  })
}

async function chooseFile(wrapper: ReturnType<typeof open>, content: string, name = 'scene.json', size?: number) {
  const input = wrapper.get('[data-testid="foundry-import-file"]')
  const file = new File([content], name, { type: 'application/json' })
  if (size !== undefined) Object.defineProperty(file, 'size', { value: size })
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

async function runPreview(wrapper: ReturnType<typeof open>) {
  await wrapper.get('[data-testid="foundry-import-preview"]').trigger('click')
  await flushPromises()
}

function commitBody(apiMock: ReturnType<typeof vi.fn>) {
  const calls = apiMock.mock.calls as unknown as [string, RequestInit][]
  const call = calls.find(entry => entry[0].endsWith('/commit'))
  expect(call, 'expected a commit request').toBeTruthy()
  return JSON.parse(call![1].body as string)
}

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
  localStorage.clear()
})

describe('ImportWizard', () => {
  it('previews the selected file and renders the plan with the conversion report', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([item()]))
      : Promise.reject(new Error(`unexpected ${path}`)))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, JSON.stringify({ _id: 'x', name: 'Plaza' }))
    await runPreview(wrapper)

    expect(apiMock).toHaveBeenCalledWith('/games/one/foundry/scenes/preview', expect.objectContaining({ method: 'POST' }))
    const body = JSON.parse((apiMock.mock.calls as unknown as [string, RequestInit][])[0][1].body as string)
    expect(body.source_version).toBe('v14')
    expect(body.scene).toEqual({ _id: 'x', name: 'Plaza' })
    expect(wrapper.find('[data-testid="foundry-import-plan"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="foundry-level-badge"]').text()).toContain('Convertible')
    expect(wrapper.get('[data-testid="foundry-plan-item"]').text()).toContain('Plaza')
  })

  it('defaults a new scene to create and commits with digest, policies and operation id', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([item()]))
      : Promise.resolve(commitResponse()))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '{}')
    await runPreview(wrapper)

    const select = wrapper.get('[data-testid="foundry-import-policy"]').element as HTMLSelectElement
    expect(select.value).toBe('create')
    await wrapper.get('[data-testid="foundry-import-commit"]').trigger('click')
    await flushPromises()

    const body = commitBody(apiMock)
    expect(body.plan_digest).toBe('sha256:abc')
    expect(body.plan.schema_version).toBe(1)
    expect(body.policies).toEqual({ 'scene-a': { action: 'create' } })
    expect(body.operation_id).toMatch(/^import-[A-Za-z0-9-]+$/)
    expect(wrapper.find('[data-testid="foundry-import-receipt"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="foundry-receipt-item"]').text()).toContain('Created')
    expect(wrapper.emitted('imported')).toHaveLength(1)
  })

  it('defaults an existing scene to update with expected_revision and hides create', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([item({ conflict: 'existing', existing_revision: 3 })]))
      : Promise.resolve(commitResponse({ receipt: { operation_id: 'import-op', plan_digest: 'sha256:abc', source_version: 'v14', revision: 4, items: [{ canonical_id: 'scene-a', action: 'update', status: 'updated' }] } })))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '{}')
    await runPreview(wrapper)

    const select = wrapper.get('[data-testid="foundry-import-policy"]').element as HTMLSelectElement
    expect(select.value).toBe('update')
    expect([...select.options].map(option => option.value)).toEqual(['update', 'duplicate', 'skip'])
    expect(wrapper.get('[data-testid="foundry-plan-item"]').text()).toContain('Existing scene')

    await wrapper.get('[data-testid="foundry-import-commit"]').trigger('click')
    await flushPromises()
    expect(commitBody(apiMock).policies).toEqual({ 'scene-a': { action: 'update', expected_revision: 3 } })
    expect(wrapper.get('[data-testid="foundry-receipt-item"]').text()).toContain('Updated')
  })

  it('sends a skip policy without expected_revision when the GM skips the item', async () => {
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([item()]))
      : Promise.resolve(commitResponse({ receipt: { operation_id: 'import-op', plan_digest: 'sha256:abc', source_version: 'v14', revision: 0, items: [{ canonical_id: 'scene-a', action: 'skip', status: 'skipped' }] } })))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '{}')
    await runPreview(wrapper)
    await wrapper.get('[data-testid="foundry-import-policy"]').setValue('skip')
    await wrapper.get('[data-testid="foundry-import-commit"]').trigger('click')
    await flushPromises()
    expect(commitBody(apiMock).policies).toEqual({ 'scene-a': { action: 'skip' } })
    expect(wrapper.get('[data-testid="foundry-receipt-item"]').text()).toContain('Skipped')
  })

  it('shows the idempotent notice when a repeated commit returns the existing receipt', async () => {
    let idempotent = false
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([item()]))
      : Promise.resolve(commitResponse({ idempotent })))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '{}')
    await runPreview(wrapper)
    await wrapper.get('[data-testid="foundry-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="foundry-import-idempotent"]').exists()).toBe(false)

    idempotent = true
    await wrapper.get('[data-testid="foundry-import-recommit"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="foundry-import-idempotent"]').text()).toContain('already committed')
    expect(wrapper.emitted('imported')).toHaveLength(2)
  })

  it('prompts a re-preview on foundry_import_stale and recovers after previewing again', async () => {
    let stale = true
    const apiMock = vi.fn((path: string) => {
      if (path.endsWith('/preview')) return Promise.resolve(previewResponse([item()]))
      return stale
        ? Promise.reject(new ApiError('stale plan', 409, 'foundry_import_stale'))
        : Promise.resolve(commitResponse())
    })
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '{}')
    await runPreview(wrapper)
    await wrapper.get('[data-testid="foundry-import-commit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="foundry-import-stale"]').text()).toContain('Preview expired')
    expect(wrapper.find('[data-testid="foundry-import-receipt"]').exists()).toBe(false)

    stale = false
    await wrapper.get('[data-testid="foundry-import-repreview"]').trigger('click')
    await flushPromises()
    expect(apiMock.mock.calls.filter(entry => String(entry[0]).endsWith('/preview'))).toHaveLength(2)
    expect(wrapper.find('[data-testid="foundry-import-stale"]').exists()).toBe(false)

    await wrapper.get('[data-testid="foundry-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="foundry-import-receipt"]').exists()).toBe(true)
  })

  it('rejects an oversized file locally without calling the API', async () => {
    const apiMock = vi.fn()
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '{}', 'big.json', 17 * 1024 * 1024)
    expect(wrapper.get('[data-testid="foundry-import-error"]').text()).toContain('16 MiB')
    expect(apiMock).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="foundry-import-preview"]').attributes('disabled')).toBeDefined()
  })

  it('rejects a file that is not valid JSON', async () => {
    const apiMock = vi.fn()
    const wrapper = open(apiMock)
    await chooseFile(wrapper, 'this is not json')
    expect(wrapper.get('[data-testid="foundry-import-error"]').text()).toContain('not valid JSON')
    expect(apiMock).not.toHaveBeenCalled()
  })

  it('surfaces a localized preview error and recovers on retry', async () => {
    let fail = true
    const apiMock = vi.fn((path: string) => {
      if (!path.endsWith('/preview')) return Promise.reject(new Error(`unexpected ${path}`))
      return fail
        ? Promise.reject(new ApiError('raw backend text', 422, 'foundry_version_unsupported'))
        : Promise.resolve(previewResponse([item()]))
    })
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '{}')
    await runPreview(wrapper)
    expect(wrapper.get('[data-testid="foundry-import-error"]').text()).toContain('Foundry VTT version is not supported')
    expect(wrapper.find('[data-testid="foundry-import-plan"]').exists()).toBe(false)

    fail = false
    await runPreview(wrapper)
    expect(wrapper.find('[data-testid="foundry-import-error"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="foundry-import-plan"]').exists()).toBe(true)
  })

  it('renders Chinese copy when the locale is zh-CN', async () => {
    i18n.global.locale.value = 'zh-CN'
    const apiMock = vi.fn((path: string) => path.endsWith('/preview')
      ? Promise.resolve(previewResponse([item()]))
      : Promise.resolve(commitResponse()))
    const wrapper = open(apiMock)
    await chooseFile(wrapper, '{}')
    await runPreview(wrapper)
    expect(wrapper.text()).toContain('导入计划')
    expect(wrapper.get('[data-testid="foundry-level-badge"]').text()).toContain('可直接转换')
    expect(wrapper.get('[data-testid="foundry-import-commit"]').text()).toContain('提交导入')
  })
})

describe('ConversionReportPanel', () => {
  it('groups issues by the eight classes in severity order and lists warnings and missing references', () => {
    const wrapper = mount(ConversionReportPanel, {
      props: {
        report: report({
          level: 'contains_script',
          contains_script: true,
          issues: [
            { level: 'contains_script', path: 'scene.flags.macro', detail: 'inert' },
            { level: 'degraded_display', path: 'scene.walls[0]', detail: 'display only' },
            { level: 'degraded_display', path: 'scene.lights[0]', detail: 'display only' },
          ],
          warnings: ['walls are display-only'],
          missing_references: [{ kind: 'actor', path: 'scene.tokens[0].actor_ref', external_id: 'a1' }],
        }),
        scrubbedFields: ['scene.extensions.seat_token'],
      },
      global: { plugins: [i18n] },
    })

    const degraded = wrapper.get('[data-testid="foundry-issues-degraded_display"]')
    expect(degraded.findAll('li')).toHaveLength(2)
    const script = wrapper.get('[data-testid="foundry-issues-contains_script"]')
    expect(script.findAll('li')).toHaveLength(1)
    const groups = wrapper.findAll('.foundry-issue-group')
    expect(groups[0].attributes('data-testid')).toBe('foundry-issues-degraded_display')
    expect(groups[1].attributes('data-testid')).toBe('foundry-issues-contains_script')
    expect(wrapper.find('[data-testid="foundry-script-warning"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="foundry-warnings"]').text()).toContain('walls are display-only')
    expect(wrapper.get('[data-testid="foundry-missing-references"]').text()).toContain('actor')
    expect(wrapper.get('[data-testid="foundry-scrubbed-fields"]').text()).toContain('seat_token')
  })

  it('shows an empty state when there are no issues', () => {
    const wrapper = mount(ConversionReportPanel, {
      props: { report: report() },
      global: { plugins: [i18n] },
    })
    expect(wrapper.text()).toContain('No conversion issues.')
    expect(wrapper.find('.foundry-issue-group').exists()).toBe(false)
  })
})