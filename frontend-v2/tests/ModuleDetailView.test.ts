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
  marketplace: vi.fn(),
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
      marketplace: mocks.marketplace,
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
      { path: '/create', name: 'create', component: emptyView },
    ],
  })
  await router.push({ name: 'module-detail', params: { moduleId: 'castle-module' } })
  await router.isReady()
  const wrapper = mount(ModuleDetailView, { global: { plugins: [i18n, router], stubs: { ModuleResourceBrowser: true } } })
  await flushPromises()
  return { wrapper, router }
}

function onlineUpdate(overrides: Record<string, unknown> = {}) {
  return { ok: true, modules: [{ id: 'castle-module', latest_version: '1.1.0', update_available: true, ...overrides }] }
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
    mocks.marketplace.mockResolvedValue({ ok: true, modules: [] })
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

    const metadata = wrapper.get('[data-testid="module-metadata"]').text()
    expect(metadata).toContain('castle-module')
    // Readable labels first, raw host values kept for troubleshooting.
    expect(metadata).toContain('冒险模组 adventure-module')
    expect(metadata).toContain('已启用 running')
    expect(metadata).toContain('开局时按需取用，启用时不导入 catalog')
    expect(wrapper.get('[data-testid="module-compatibility"]').text()).toContain('兼容')
    expect(wrapper.get('[data-testid="module-adventures"]').text()).toContain('plugin:castle-quest')
    expect(wrapper.get('[data-testid="module-content"]').text()).toContain('NPC · 1')
    expect(wrapper.get('[data-testid="module-usage"]').text()).toContain('web|room|bot')
  })

  it('labels a module without adventures as a resource pack and keeps the canonical profile', async () => {
    mocks.detail.mockResolvedValue(detailPayload({
      adventure_count: 0,
      content_profile: 'content-pack',
      adventures: [],
      content: { npc: [] },
    }))

    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('资源包')
    expect(wrapper.text()).toContain('不含固定剧情')
    expect(wrapper.get('[data-testid="module-metadata"]').text()).toContain('content-pack')
    expect(wrapper.get('[data-testid="module-adventures"]').text()).not.toContain('用此冒险创建游戏')
  })

  it('keeps a content pack with adventures labelled by its profile', async () => {
    mocks.detail.mockResolvedValue(detailPayload({ content_profile: 'content-pack', content_delivery_mode: 'legacy_autoimport' }))

    const { wrapper } = await mountView()

    expect(wrapper.get('.module-quick-facts').text()).toContain('资源包')
    expect(wrapper.get('.module-quick-facts').text()).not.toContain('content-pack')
    expect(wrapper.get('[data-testid="module-adventures"]').text()).toContain('plugin:castle-quest')
    expect(wrapper.get('[data-testid="module-metadata"]').text()).toContain('启用时自动导入包里的内容 legacy_autoimport')
  })

  it('shows unknown host values as they are', async () => {
    mocks.detail.mockResolvedValue(detailPayload({ content_profile: 'theme-module', status: 'paused', content_delivery_mode: 'streamed' }))

    const { wrapper } = await mountView()

    expect(wrapper.get('.module-quick-facts').text()).toContain('内容包')
    const metadata = wrapper.get('[data-testid="module-metadata"]').text()
    expect(metadata).toContain('paused paused')
    expect(metadata).toContain('streamed streamed')
  })

  it('shows installed adventures in a disabled package without promising playability', async () => {
    mocks.detail.mockResolvedValue(detailPayload({ status: 'disabled', inventory_status: 'disabled' }))
    const { wrapper } = await mountView()
    const adventures = wrapper.get('[data-testid="module-adventures"]')
    expect(adventures.text()).toContain('已安装冒险')
    expect(adventures.text()).toContain('启用后')
    expect(adventures.find('button').attributes('disabled')).toBeDefined()
    expect(adventures.text()).not.toContain('可玩冒险')
  })

  it('does not describe unreadable installed content as an empty resource pack', async () => {
    mocks.detail.mockResolvedValue(detailPayload({ adventure_count: null, content_counts: null, inventory_status: 'unknown', adventures: [] }))
    const { wrapper } = await mountView()
    expect(wrapper.get('[data-testid="module-adventures"]').text()).toContain('内容清单暂不可读')
    expect(wrapper.text()).not.toContain('不含固定剧情')
  })

  it('keeps content pack and library exits visible when detail loading fails', async () => {
    mocks.detail.mockRejectedValue(new Error('detail unavailable'))
    const { wrapper } = await mountView()

    const navigation = wrapper.get('[data-testid="module-fallback-navigation"]')
    expect(navigation.find('a[href="#/library"]').exists()).toBe(true)
    expect(navigation.find('a[href="#/modules"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('detail unavailable')
  })

  it('disables destructive buttons when the server guard blocks them', async () => {
    const bound = [{ game_key: 'web|room|bot', adventure_id: 'plugin:castle-quest', run_id: 'r1' }]
    mocks.detail.mockResolvedValue(detailPayload({
      bound_games: bound,
      actions: { update: guard(false, bound), disable: guard(false, bound), uninstall: guard(false, bound) },
    }))
    mocks.marketplace.mockResolvedValue(onlineUpdate())

    const { wrapper } = await mountView()

    expect(wrapper.get('[data-testid="module-action-blocked"]').text()).toContain('1 个存档正在使用此内容包')
    expect(buttonByText(wrapper, '更新到 1.1.0').attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '禁用').attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '卸载').attributes('disabled')).toBeDefined()
    expect(mocks.uninstall).not.toHaveBeenCalled()
  })

  it('offers update only when the online catalog has a newer version', async () => {
    // Imported from a file / not listed online: nothing to update from.
    const { wrapper } = await mountView()
    expect(mocks.marketplace).toHaveBeenCalledWith('castle-module')
    expect(wrapper.findAll('button').some(button => button.text().startsWith('更新'))).toBe(false)

    mocks.marketplace.mockResolvedValue(onlineUpdate({ update_available: false, latest_version: '1.0.0' }))
    const current = await mountView()
    expect(current.wrapper.findAll('button').some(button => button.text().startsWith('更新'))).toBe(false)

    // A substring match on the keyword must not borrow another pack's update.
    mocks.marketplace.mockResolvedValue({ ok: true, modules: [{ id: 'castle-module-extra', latest_version: '2.0.0', update_available: true }] })
    const other = await mountView()
    expect(other.wrapper.findAll('button').some(button => button.text().startsWith('更新'))).toBe(false)
  })

  it('updates to the newer catalog version and reloads the page', async () => {
    mocks.marketplace.mockResolvedValue(onlineUpdate())
    const { wrapper } = await mountView()
    mocks.detail.mockClear()

    await buttonByText(wrapper, '更新到 1.1.0').trigger('click')
    await flushPromises()

    expect(mocks.update).toHaveBeenCalledWith('castle-module')
    expect(wrapper.get('[role="status"]').text()).toBe('操作已完成。')
    expect(mocks.detail).toHaveBeenCalled()
  })

  it('says so when the installed release is already current', async () => {
    mocks.marketplace.mockResolvedValue(onlineUpdate())
    mocks.update.mockResolvedValue({ ok: true, up_to_date: true })
    const { wrapper } = await mountView()

    await buttonByText(wrapper, '更新到 1.1.0').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="status"]').text()).toBe('已经是最新版本。')
  })

  it('stays usable when the online catalog cannot be reached', async () => {
    mocks.marketplace.mockRejectedValue(new Error('offline'))
    const { wrapper } = await mountView()

    expect(wrapper.find('.error-text').exists()).toBe(false)
    expect(wrapper.findAll('button').some(button => button.text().startsWith('更新'))).toBe(false)
    expect(buttonByText(wrapper, '禁用').attributes('disabled')).toBeUndefined()
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

  it('launches an adventure with the exact module and adventure identity', async () => {
    const { wrapper, router } = await mountView()
    await buttonByText(wrapper, '用此冒险创建游戏').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('create')
    expect(router.currentRoute.value.query).toEqual({ module_id: 'castle-module', adventure_id: 'plugin:castle-quest' })
  })

  it('shows how many entries each book holds and whether it is in the library yet', async () => {
    mocks.detail.mockResolvedValue(detailPayload({
      adventure_count: 0, adventures: [], content_profile: 'content-pack',
      lorebooks: [
        { id: 'module_book_castle_archive', name: 'Castle Archive', description: '', language: 'zh-CN', enabled: true, source_kind: 'module', source_id: 'castle-module', catalog: true, entry_count: 18 },
        { id: 'module_book_castle_notes', name: 'Castle Notes', description: '', language: 'zh-CN', enabled: true, source_kind: 'module', source_id: 'castle-module', catalog: false, entry_count: 0 },
      ],
    }))
    const { wrapper } = await mountView()
    const section = wrapper.get('[data-testid="module-lorebooks"]')

    // 只给书名的话，18 条那本和空主册在页面上无从分辨。
    expect(section.text()).toContain('18 条')
    expect(section.text()).toContain('包内只读')
    expect(section.text()).toContain('已在资料册中')
    expect(section.text()).toContain('不随安装写入你的书库')
  })

  it('offers a pack-wide launch for resource packs that declare lorebooks', async () => {
    const lorebooks = [{
      id: 'module_book_castle_archive', name: 'Castle Archive', description: '', language: 'zh-CN',
      enabled: true, source_kind: 'module', source_id: 'castle-module',
    }]
    mocks.detail.mockResolvedValue(detailPayload({
      adventure_count: 0, adventures: [], content_profile: 'content-pack', lorebooks,
    }))
    const { wrapper, router } = await mountView()

    expect(wrapper.get('[data-testid="module-lorebooks"]').text()).toContain('Castle Archive')
    await buttonByText(wrapper, '用这个包开新冒险').trigger('click')
    await flushPromises()

    // 资源包没有固定剧情：深链只带包身份，创建页据此把它的资料册作为候选。
    expect(router.currentRoute.value.name).toBe('create')
    expect(router.currentRoute.value.query).toEqual({ module_id: 'castle-module' })
  })

  it('blocks the pack-wide launch while the pack is disabled', async () => {
    mocks.detail.mockResolvedValue(detailPayload({
      status: 'disabled', adventure_count: 0, adventures: [], content_profile: 'content-pack',
      lorebooks: [{
        id: 'module_book_castle_archive', name: 'Castle Archive', description: '', language: 'zh-CN',
        enabled: true, source_kind: 'module', source_id: 'castle-module',
      }],
    }))
    const { wrapper } = await mountView()

    expect(buttonByText(wrapper, '用这个包开新冒险').attributes('disabled')).toBeDefined()
  })

  it('blocks adventure launch for disabled or incompatible modules', async () => {    mocks.detail.mockResolvedValue(detailPayload({ status: 'disabled' }))
    const { wrapper } = await mountView()
    expect(buttonByText(wrapper, '用此冒险创建游戏').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('启用内容包并解决兼容性问题后再创建游戏')
  })

  it('shows compatibility blockers before technical details and keeps resource copy browsing mounted', async () => {
    mocks.compatibility.mockResolvedValue({ ok: false, module_id: 'castle-module', blockers: ['runtime missing'], warnings: [] })
    const { wrapper } = await mountView()
    const compatibility = wrapper.get('[data-testid="module-compatibility"]')
    expect(compatibility.attributes('open')).toBeDefined()
    expect(wrapper.get('[data-testid="module-adventures"]')).toBeTruthy()
    expect(wrapper.findComponent({ name: 'ModuleResourceBrowser' }).exists()).toBe(true)
    expect(wrapper.get('[data-testid="module-metadata"]').element.tagName).toBe('DETAILS')
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

    expect(wrapper.text()).toContain('该内容包正在被存档使用')
    expect(wrapper.get('[data-testid="module-action-blocked"]').text()).toContain('1 个存档正在使用此内容包')
    expect(buttonByText(wrapper, '禁用').attributes('disabled')).toBeDefined()
  })
})
