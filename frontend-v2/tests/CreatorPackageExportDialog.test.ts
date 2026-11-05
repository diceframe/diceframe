import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import CreatorPackageExportDialog from '../src/features/creator/CreatorPackageExportDialog.vue'
import type { CreatorPackageExportMode } from '../src/features/creator/copy'

const mocks = vi.hoisted(() => ({ api: vi.fn(), apiBlob: vi.fn() }))
vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api, apiBlob: mocks.apiBlob }
})

const fixedAdventure = {
  adventure_id: 'night-shift', source_kind: 'user', source_id: '', version: '1.0.0', format: 'adventure-bundle',
  world_policy: 'fixed', recommended_world_id: 'office', required_runtime: { id: 'core:generic', minimum_version: 1 },
  name: '夜班', summary: '', estimated_minutes: 60, compatibility: 'compatible', incompatibility_reasons: [], custom: true,
}

function respond(path: string) {
  if (path === '/worlds') return { worlds: [{ id: 'office', name: '深夜办公室' }] }
  if (path.startsWith('/adventures')) return { ok: true, adventures: [fixedAdventure] }
  if (path.startsWith('/lorebooks')) return { books: [{ id: 'handbook', name: '员工手册', entry_count: 3 }] }
  if (path.startsWith('/rules')) return { rules: [{ rule_id: 'office-rules', rule_name: '加班规则' }] }
  throw new Error(`unexpected ${path}`)
}

async function render(mode: CreatorPackageExportMode) {
  const wrapper = mount(CreatorPackageExportDialog, { props: { mode }, global: { plugins: [i18n], stubs: { Teleport: true } } })
  await flushPromises()
  await wrapper.get('input[placeholder]').setValue(mode === 'adventure' ? 'night-module' : 'office-resources')
  await wrapper.findAll('.creator-package-grid input')[1].setValue('测试包')
  return wrapper
}

function exportBody(): Record<string, unknown> {
  const [path, init] = mocks.apiBlob.mock.calls[0] as [string, { body: string }]
  expect(path).toBe('/plugins/export')
  return JSON.parse(init.body) as Record<string, unknown>
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  mocks.api.mockImplementation(async (path: string) => respond(path))
  mocks.apiBlob.mockResolvedValue({ blob: async () => new Blob(['pack']) })
  URL.createObjectURL = vi.fn(() => 'blob:pack')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => { vi.clearAllMocks() })

describe('CreatorPackageExportDialog', () => {
  it('exports a resource pack without listing or sending adventures', async () => {
    const wrapper = await render('resource')
    expect(wrapper.text()).toContain('导出资源包')
    expect(mocks.api.mock.calls.some(([path]) => String(path).startsWith('/adventures'))).toBe(false)
    expect(wrapper.find('[data-testid="creator-export-adventures"]').exists()).toBe(false)

    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    expect(wrapper.text()).toContain('至少选择一个世界、规则或资料册。')
    expect(mocks.apiBlob).not.toHaveBeenCalled()

    await wrapper.get('select').setValue('office')
    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await flushPromises()
    expect(exportBody()).toMatchObject({ world_id: 'office', adventure_refs: [] })
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('requires at least one adventure for an adventure module', async () => {
    const wrapper = await render('adventure')
    await wrapper.get('select').setValue('office')
    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    expect(wrapper.text()).toContain('至少选择一个冒险。')
    expect(mocks.apiBlob).not.toHaveBeenCalled()
  })

  it('previews runtime and world policy, and warns when a fixed world is not exported', async () => {
    const wrapper = await render('adventure')
    expect(wrapper.find('[data-testid="creator-export-preview"]').exists()).toBe(false)

    await wrapper.get('[data-testid="creator-export-adventures"] input[type="checkbox"]').setValue(true)
    const preview = wrapper.get('[data-testid="creator-export-preview"]')
    expect(preview.text()).toContain('core:generic ≥ 1')
    expect(preview.text()).toContain('固定世界')
    expect(preview.text()).toContain('GM')
    expect(wrapper.findAll('[data-testid="creator-export-warning"]').map(item => item.text()))
      .toEqual(['「夜班」固定使用世界「深夜办公室」，但这次没有一并导出；安装方没有这个世界时无法开局。'])

    await wrapper.get('select').setValue('office')
    expect(wrapper.find('[data-testid="creator-export-warning"]').exists()).toBe(false)
    const updated = wrapper.get('[data-testid="creator-export-preview"]').text()
    expect(updated).toContain('没有发现会阻碍开局的问题。')
    expect(updated).toContain('世界 1')

    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await flushPromises()
    expect(exportBody()).toMatchObject({
      plugin_id: 'night-module', world_id: 'office',
      adventure_refs: [{ adventure_id: 'night-shift', source_kind: 'user', source_id: '' }],
    })
  })

  it('does not treat the context-free listing compatibility as an export problem', async () => {
    // GET /adventures without rule/world always reports ruleset_runtime_unavailable.
    mocks.api.mockImplementation(async (path: string) => path.startsWith('/adventures')
      ? { ok: true, adventures: [{ ...fixedAdventure, world_policy: 'portable', compatibility: 'incompatible', incompatibility_reasons: ['ruleset_runtime_unavailable'] }] }
      : respond(path))
    const wrapper = await render('adventure')
    await wrapper.get('[data-testid="creator-export-adventures"] input[type="checkbox"]').setValue(true)
    expect(wrapper.find('[data-testid="creator-export-warning"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="creator-export-preview"]').text()).toContain('没有发现会阻碍开局的问题。')
  })
})
