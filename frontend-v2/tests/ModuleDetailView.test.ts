import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { i18n } from '../src/i18n'
import { ApiError } from '../src/api/client'
import ModuleDetailView from '../src/features/modules/ModuleDetailView.vue'

const mocks = vi.hoisted(() => ({
  detail: vi.fn(),
  compatibility: vi.fn(),
  usages: vi.fn(),
  update: vi.fn(),
  disable: vi.fn(),
  enable: vi.fn(),
  uninstall: vi.fn(),
  confirm: vi.fn(),
}))

vi.mock('../src/api/modules', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/modules')>()
  return {
    ...actual,
    moduleApi: {
      ...actual.moduleApi,
      detail: mocks.detail,
      compatibility: mocks.compatibility,
      usages: mocks.usages,
      update: mocks.update,
      disable: mocks.disable,
      enable: mocks.enable,
      uninstall: mocks.uninstall,
    },
  }
})

vi.mock('../src/composables/useConfirm', () => ({
  useConfirm: () => ({ confirm: mocks.confirm }),
}))

function guard(allowed: boolean, games: Array<Record<string, string>> = []) {
  return { allowed, reason: allowed ? '' : 'MODULE_IN_USE', games }
}

function detailPayload(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    module: {
      id: 'castle-module',
      name: 'Castle Module',
      version: '1.0.0',
      content_profile: 'adventure-module',
      content_delivery_mode: 'catalog',
      is_module: true,
      status: 'running',
      adventure_count: 1,
      content_counts: { npc: 1 },
      content: { npc: [{ key: 'himmel', title: 'Himmel', description: '' }] },
      adventures: [{
        adventure_id: 'plugin:castle-quest', version: '1.0.0',
        format: 'diceframe:adventure-graph-v1', directory_id: 'castle',
      }],
      bound_games: [],
      actions: {
        update: guard(true), disable: guard(true), uninstall: guard(true), overwrite: guard(true),
      },
      ...overrides,
    },
  }
}

async function mountView() {
  const emptyView = defineComponent({ template: '<div />' })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/modules', name: 'modules', component: emptyView },
      { path: '/modules/:moduleId', name: 'module-detail', component: ModuleDetailView },
    ],
  })
  await router.push({ name: 'module-detail', params: { moduleId: 'castle-module' } })
  await router.isReady()
  const wrapper = mount(ModuleDetailView, { global: { plugins: [i18n, router] } })
  await flushPromises()
  return { wrapper, router }
}

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find(candidate => candidate.text() === text)
  if (!button) throw new Error(`未找到按钮：${text}`)
  return button
}

describe('ModuleDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18n.global.locale.value = 'zh-CN'
    mocks.detail.mockResolvedValue(detailPayload())
    mocks.compatibility.mockResolvedValue({ ok: true, module_id: 'castle-module', blockers: [], warnings: [] })
    mocks.usages.mockResolvedValue({ ok: true, module_id: 'castle-module', usages: [], total_games: 0 })
    mocks.confirm.mockResolvedValue(true)
    mocks.update.mockResolvedValue({ ok: true })
    mocks.disable.mockResolvedValue({ ok: true })
    mocks.uninstall.mockResolvedValue({ ok: true })
  })

  it('shows metadata, compatibility, adventures, content counts, and usage saves', async () => {
    mocks.detail.mockResolvedValue(detailPayload({
      bound_games: [{ game_key: 'web|room|bot', adventure_id: 'plugin:castle-quest', run_id: 'r1' }],
      actions: { update: guard(false, [{ game_key: 'web|room|bot', adventure_id: 'plugin:castle-quest', run_id: 'r1' }]) },
    }))
    mocks.usages.mockResolvedValue({
      ok: true,
      module_id: 'castle-module',
      usages: [{
        adventure_id: 'plugin:castle-quest',
        games: [{ game_key: 'web|room|bot', run_id: 'r1', content_digest: 'sha256:deadbeef' }],
      }],
      total_games: 1,
    })

    const { wrapper } = await mountView()

    expect(wrapper.get('[data-testid="module-metadata"]').text()).toContain('castle-module')
    expect(wrapper.get('[data-testid="module-metadata"]').text()).toContain('catalog')
    expect(wrapper.get('[data-testid="module-compatibility"]').text()).toContain('兼容')
    expect(wrapper.get('[data-testid="module-adventures"]').text()).toContain('plugin:castle-quest')
    expect(wrapper.get('[data-testid="module-content"]').text()).toContain('npc · 1')
    expect(wrapper.get('[data-testid="module-usage"]').text()).toContain('web|room|bot')
  })

  it('disables destructive buttons when the server guard blocks them', async () => {
    const bound = [{ game_key: 'web|room|bot', adventure_id: 'plugin:castle-quest', run_id: 'r1' }]
    mocks.detail.mockResolvedValue(detailPayload({
      bound_games: bound,
      actions: { update: guard(false, bound), disable: guard(false, bound), uninstall: guard(false, bound) },
    }))

    const { wrapper } = await mountView()

    expect(wrapper.get('[data-testid="module-action-blocked"]').text()).toContain('1 个存档正在使用此模组')
    expect(buttonByText(wrapper, '更新').attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '禁用').attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '卸载').attributes('disabled')).toBeDefined()
    expect(mocks.uninstall).not.toHaveBeenCalled()
  })

  it('runs guarded actions only after confirmation and refreshes the guard', async () => {
    const { wrapper } = await mountView()

    await buttonByText(wrapper, '禁用').trigger('click')
    await flushPromises()
    expect(mocks.disable).toHaveBeenCalledWith('castle-module')

    await buttonByText(wrapper, '卸载').trigger('click')
    await flushPromises()
    expect(mocks.confirm).toHaveBeenCalled()
    expect(mocks.uninstall).toHaveBeenCalledWith('castle-module')
  })

  it('offers enable instead of disable for a disabled module', async () => {
    mocks.detail.mockResolvedValue(detailPayload({ status: 'disabled' }))

    const { wrapper } = await mountView()

    await buttonByText(wrapper, '启用').trigger('click')
    await flushPromises()

    expect(mocks.enable).toHaveBeenCalledWith('castle-module')
  })

  it('surfaces the server MODULE_IN_USE verdict when a save appears after loading', async () => {
    const bound = [{ game_key: 'web|room|bot', adventure_id: 'plugin:castle-quest', run_id: 'r1' }]
    mocks.disable.mockRejectedValue(
      new ApiError('module is used by 1 game(s)', 409, 'MODULE_IN_USE'),
    )
    // 点击后重读详情：此时服务端 guard 已经变成 blocked。
    mocks.detail
      .mockResolvedValueOnce(detailPayload())
      .mockResolvedValue(detailPayload({
        bound_games: bound,
        actions: { update: guard(false, bound), disable: guard(false, bound), uninstall: guard(false, bound) },
      }))

    const { wrapper } = await mountView()
    await buttonByText(wrapper, '禁用').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('该模组正在被存档使用')
    expect(wrapper.get('[data-testid="module-action-blocked"]').text()).toContain('1 个存档正在使用此模组')
    expect(buttonByText(wrapper, '禁用').attributes('disabled')).toBeDefined()
  })
})
