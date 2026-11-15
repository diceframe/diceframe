import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { i18n } from '../src/i18n'
import { registerNaiveApis } from '../src/composables/useNaiveBridge'
import ModulesView from '../src/features/modules/ModulesView.vue'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  list: vi.fn(),
  marketplace: vi.fn(),
  previewImport: vi.fn(),
  importModule: vi.fn(),
  installFromMarketplace: vi.fn(),
  setRunning: vi.fn(),
  update: vi.fn(),
  hubDetail: vi.fn(),
  hubReadme: vi.fn(),
  hubRatings: vi.fn(),
  setHubRating: vi.fn(),
}))

vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api }
})

vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))

vi.mock('../src/api/plugins', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/plugins')>()
  return {
    ...actual,
    pluginApi: {
      ...actual.pluginApi,
      setRunning: mocks.setRunning,
      hubDetail: mocks.hubDetail,
      hubReadme: mocks.hubReadme,
      hubRatings: mocks.hubRatings,
      setHubRating: mocks.setHubRating,
    },
  }
})

vi.mock('../src/api/modules', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/modules')>()
  return {
    ...actual,
    moduleApi: {
      ...actual.moduleApi,
      list: mocks.list,
      marketplace: mocks.marketplace,
      previewImport: mocks.previewImport,
      import: mocks.importModule,
      installFromMarketplace: mocks.installFromMarketplace,
      update: mocks.update,
    },
  }
})

const installed = {
  ok: true,
  modules: [{
    id: 'castle-module', name: 'Castle Module', version: '1.0.0',
    content_profile: 'adventure-module', content_delivery_mode: 'catalog',
    is_module: true, status: 'running', adventure_count: 1,
    content_counts: { npc: 2 },
  }, {
    id: 'dormant-module', name: 'Dormant Module', version: '0.4.0',
    content_profile: 'content-pack', content_delivery_mode: 'catalog',
    is_module: false, status: 'disabled', adventure_count: 0,
    content_counts: {},
  }],
}

const online = {
  ok: true,
  modules: [{
    id: 'other-module', name: 'Other Module', version: '0.9.0', latest_version: '0.9.0',
    description: '另一套模组', content_profile: 'adventure-module',
    content_delivery_mode: 'catalog', adventure_count: 2, ruleset_targets: ['core:dnd2024'],
    capabilities: ['adventure'],
    languages: ['zh-CN'], tags: ['adventure'], trust_level: 'community',
    distribution: 'repository', repository_url: '', release_url: '', stars: 0,
    installed: false, installed_version: '', update_available: false,
    installable: true, verification_error: '', needs_core_update: false, min_app_version: '',
  }, {
    id: 'castle-module', name: 'Castle Module', version: '1.0.0', latest_version: '1.2.0',
    description: '', content_profile: 'adventure-module', content_delivery_mode: 'catalog',
    adventure_count: 1, ruleset_targets: ['core:dnd2024'], capabilities: ['adventure'],
    languages: ['zh-CN'], tags: [], trust_level: 'official', distribution: 'repository',
    repository_url: '', release_url: '', stars: 3, installed: true,
    installed_version: '1.0.0', update_available: true, installable: true,
    verification_error: '', needs_core_update: false, min_app_version: '',
  }],
}

async function mountView(query: Record<string, string> = {}) {
  const emptyView = defineComponent({ template: '<div />' })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/modules', name: 'modules', component: ModulesView },
      { path: '/library', name: 'library', component: emptyView },
      { path: '/modules/:moduleId', name: 'module-detail', component: emptyView },
      { path: '/play', name: 'play', component: emptyView },
      { path: '/creator', name: 'creator', component: emptyView },
    ],
  })
  await router.push({ name: 'modules', query })
  await router.isReady()
  const wrapper = mount(ModulesView, { global: { plugins: [i18n, router], stubs: { Teleport: true } } })
  await flushPromises()
  return { wrapper, router }
}

// 「我的内容包」是下拉面板：默认收起，先展开再断言。
async function openMyPanel(wrapper: Awaited<ReturnType<typeof mountView>>['wrapper']) {
  await wrapper.get('.modules-my-btn').trigger('click')
  await flushPromises()
  return wrapper.get('[data-testid="modules-installed"]')
}

// App Store 货架/筛选网格里的卡片按名字找（筛选网格不透传 data-testid）。
function shelfCardNames(wrapper: Awaited<ReturnType<typeof mountView>>['wrapper'], shelfTestId: string): string[] {
  return wrapper.findAll(`[data-testid="${shelfTestId}"] .content-pack-card .cp-name`).map(node => node.text())
}

function gridCardNames(wrapper: Awaited<ReturnType<typeof mountView>>['wrapper']): string[] {
  return wrapper.findAll('.modules-search-grid .content-pack-card .cp-name').map(node => node.text())
}

describe('ModulesView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18n.global.locale.value = 'zh-CN'
    mocks.list.mockResolvedValue(installed)
    mocks.marketplace.mockResolvedValue(online)
    mocks.api.mockResolvedValue({ adventure: null })
    try { sessionStorage.removeItem('df_modules_panel') } catch { /* ignore */ }
  })

  it('separates installed packs (panel), local import, and online shelves', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.get('h1').text()).toBe('内容包')
    // 已安装收进「我的内容包」下拉面板；禁用中的模组仍然列出（否则无法再启用）。
    const panel = await openMyPanel(wrapper)
    expect(panel.text()).toContain('Castle Module')
    expect(panel.text()).toContain('Dormant Module')
    expect(panel.text()).toContain('包含 1 个冒险')
    expect(panel.find('input[type="file"]').exists()).toBe(true)
    // 在线内容包走商店货架。
    expect(shelfCardNames(wrapper, 'modules-shelf-adventure')).toContain('Other Module')
    wrapper.unmount()
  })

  it('keeps the return-to-library action in the page header', async () => {
    const { wrapper, router } = await mountView()
    await wrapper.get('.content-page-heading-actions a[href="/library"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('library')
    wrapper.unmount()
  })

  it('keeps a failed installed list actionable with a retry path', async () => {
    mocks.list.mockRejectedValueOnce(new Error('installed unavailable'))
    const { wrapper } = await mountView()
    expect(wrapper.text()).toContain('installed unavailable')
    const panel = await openMyPanel(wrapper)
    const refresh = panel.findAll('button').find(button => button.text() === '刷新')
    expect(refresh).toBeDefined()
    mocks.list.mockResolvedValueOnce(installed)
    await refresh!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="modules-installed"]').text()).toContain('Castle Module')
  })

  it('keeps the core ruleset chips even when no pack declares one', async () => {
    const base = online.modules[0]!
    mocks.marketplace.mockResolvedValue({
      ok: true,
      modules: [
        { ...base, id: 'a-pack', name: 'a-pack', ruleset_targets: [], ruleset_declared: false },
        { ...base, id: 'b-pack', name: 'b-pack', ruleset_targets: [], ruleset_declared: false },
      ],
    })
    const { wrapper } = await mountView()

    // 线上内容包全部未声明时，CoC / D&D 2024 / Freeform 入口也不能消失（否则用户没法筛）。
    const chips = wrapper.findAll('.modules-category-chip').map(chip => chip.text())
    expect(chips).toContain('CoC')
    expect(chips).toContain('D&D 2024')
    expect(chips).toContain('Freeform')
    wrapper.unmount()
  })

  it('filters by ruleset chip while 全部 keeps undeclared packs visible', async () => {
    const base = online.modules[0]!
    const withRuleset = (id: string, targets: string[], extra: Record<string, unknown> = {}) => ({
      ...base, id, name: id, ruleset_targets: targets, ...extra,
    })
    mocks.marketplace.mockResolvedValue({
      ok: true,
      modules: [
        withRuleset('coc-pack', ['core:coc7']),
        withRuleset('dnd-pack', ['core:dnd2024']),
        withRuleset('undeclared-pack', []),
      ],
    })
    const { wrapper } = await mountView()

    // 全部：三种包都可见。
    expect(shelfCardNames(wrapper, 'modules-shelf-adventure')).toEqual(['coc-pack', 'dnd-pack', 'undeclared-pack'])
    // 点 CoC chip：只留声明 CoC 的包。
    const cocChip = wrapper.findAll('.modules-category-chip').find(chip => chip.text() === 'CoC')!
    await cocChip.trigger('click')
    await flushPromises()
    expect(gridCardNames(wrapper)).toEqual(['coc-pack'])
    wrapper.unmount()
  })

  it('clears profile filters with the reset button and shows the filtered-empty hint', async () => {
    const { wrapper } = await mountView()
    // 打开筛选面板，用 content-pack 一定能把两份 fixture 都筛空。
    await wrapper.get('.modules-filter-toggle').trigger('click')
    const profileSelect = wrapper.findAll('[data-testid="modules-market-filters"] select')
      .find(select => select.findAll('option').some(option => option.element.value === 'content-pack'))!
    await profileSelect.setValue('content-pack')
    await flushPromises()

    const grid = wrapper.get('.modules-search-grid')
    expect(grid.text()).toContain('没有符合筛选条件的内容包。')

    await wrapper.get('.module-filter-reset').trigger('click')
    await flushPromises()
    expect(shelfCardNames(wrapper, 'modules-shelf-adventure')).toContain('Other Module')
    wrapper.unmount()
  })

  it('sorts the adventure shelf by downloads and by likes', async () => {
    const base = online.modules[0]!
    mocks.marketplace.mockResolvedValue({
      ok: true,
      modules: [
        { ...base, id: 'quiet-pack', name: 'Quiet Pack', stats: { downloads_total: 10, likes: 30 } },
        { ...base, id: 'busy-pack', name: 'Busy Pack', stats: { downloads_total: 500, likes: 5 } },
        { ...base, id: 'liked-pack', name: 'Liked Pack', stats: { downloads_total: 100, likes: 90 } },
      ],
    })
    const { wrapper } = await mountView()
    const order = () => shelfCardNames(wrapper, 'modules-shelf-adventure')

    // 默认保留市场给的顺序（推荐）。
    expect(order()).toEqual(['Quiet Pack', 'Busy Pack', 'Liked Pack'])

    // 排序在筛选面板里；冒险货架吃同一份排序结果。
    await wrapper.get('.modules-filter-toggle').trigger('click')
    await wrapper.get('[data-testid="modules-market-sort"]').setValue('downloads')
    expect(order()).toEqual(['Busy Pack', 'Liked Pack', 'Quiet Pack'])

    await wrapper.get('[data-testid="modules-market-sort"]').setValue('likes')
    expect(order()).toEqual(['Liked Pack', 'Quiet Pack', 'Busy Pack'])
    wrapper.unmount()
  })

  it('marks installed online modules and installs missing ones from the shelves', async () => {
    mocks.installFromMarketplace.mockResolvedValue({ ok: true })
    const { wrapper } = await mountView()

    const fresh = wrapper.get('[data-testid="modules-market-other-module"]')
    const install = fresh.findAll('button').find(button => button.text() === '安装')
    expect(install).toBeDefined()
    await install!.trigger('click')
    await flushPromises()
    expect(mocks.installFromMarketplace).toHaveBeenCalledWith('other-module', false)
    expect(wrapper.text()).not.toContain('签名校验失败')

    const update = wrapper.get('[data-testid="modules-market-castle-module"]')
    // 已安装 + 商店有新版本：卡片给「更新」入口而不是「已安装」。
    expect(update.findAll('button').map(button => button.text())).toContain('更新')
    expect(update.text()).toContain('1 个冒险')
  })

  it('offers to enable a freshly installed pack and starts it on confirm', async () => {
    mocks.installFromMarketplace.mockResolvedValue({ ok: true, id: 'other-module', name: 'Other Module', enabled: false })
    mocks.setRunning.mockResolvedValue({ ok: true })
    const dialogs: Array<{ positiveText?: string; onPositiveClick?: () => void }> = []
    registerNaiveApis(
      { success: () => {}, error: () => {}, info: () => {}, warning: () => {} } as never,
      { info: (config: never) => dialogs.push(config), warning: (config: never) => dialogs.push(config), error: (config: never) => dialogs.push(config), success: (config: never) => dialogs.push(config) } as never,
    )
    const { wrapper } = await mountView()

    const fresh = wrapper.get('[data-testid="modules-market-other-module"]')
    await fresh.findAll('button').find(button => button.text() === '安装')!.trigger('click')
    await vi.waitFor(() => expect(dialogs).toHaveLength(1))
    expect(dialogs[0]!.positiveText).toBe('现在启用')

    dialogs[0]!.onPositiveClick?.()
    await flushPromises()

    expect(mocks.setRunning).toHaveBeenCalledWith('other-module', true)
    wrapper.unmount()
  })

  it('shows the marketplace error when the install endpoint returns a failed result', async () => {
    mocks.installFromMarketplace.mockResolvedValue({ ok: false, error: '签名校验失败' })
    const { wrapper } = await mountView()

    const fresh = wrapper.get('[data-testid="modules-market-other-module"]')
    await fresh.findAll('button').find(button => button.text() === '安装')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('签名校验失败')
  })

  it('reports an unreachable marketplace instead of an empty one', async () => {
    mocks.marketplace.mockResolvedValue({
      ok: false, modules: [], error_code: 'MODULE_MARKETPLACE_UNAVAILABLE', error: 'mirror down',
    })
    const { wrapper } = await mountView()
    expect(wrapper.text()).toContain('mirror down')
  })

  it('explains how packs get listed with a Workshop exit and a direct submission exit', async () => {
    const { wrapper } = await mountView()
    expect(wrapper.find('[data-testid="modules-submit-guide"]').exists()).toBe(false)

    await wrapper.get('.modules-submit-link').trigger('click')
    const guide = wrapper.get('[data-testid="modules-submit-guide"]')
    expect(guide.findAll('li')).toHaveLength(3)
    expect(guide.get('[data-testid="modules-submit-go-creator"]').attributes('href')).toBe('/creator')
    const direct = guide.get('[data-testid="modules-submit-direct"]')
    expect([direct.attributes('target'), direct.attributes('rel')]).toEqual(['_blank', 'noopener'])
    const url = new URL(direct.attributes('href') || '')
    expect(`${url.origin}${url.pathname}`).toBe('https://github.com/diceframe/diceframe-plugins/issues/new')
    expect(Object.fromEntries(url.searchParams)).toEqual({ template: 'add-plugin.yml', title: '[Plugin Submission]' })
  })

  it('points authors from an empty online shelf to the Workshop', async () => {
    mocks.marketplace.mockResolvedValue({ ok: true, modules: [] })
    const { wrapper } = await mountView()
    const empty = wrapper.get('[data-testid="modules-online-empty"]')
    expect(empty.text()).toContain('暂时没有在线内容包')
    expect(empty.get('a').attributes('href')).toBe('/creator')
  })

  it('does not invent a resource count that the online catalog does not provide', async () => {
    mocks.marketplace.mockResolvedValue({ ok: true, modules: [{ ...online.modules[0], adventure_count: 0 }] })
    const { wrapper } = await mountView()
    const card = wrapper.get('[data-testid="modules-market-other-module"]')
    expect(card.text()).not.toContain('0 个冒险')
  })

  it('labels a content pack that carries adventures by its profile in both lists', async () => {
    // A plain content pack may ship adventures (office-night 1.3.0); the label
    // follows content_profile and the adventures are reported separately.
    mocks.list.mockResolvedValue({ ok: true, modules: [{
      ...installed.modules[1]!, id: 'office-night', name: 'Office Night', status: 'running',
      content_delivery_mode: 'legacy_autoimport', adventure_count: 1, content_counts: { world: 1 },
    }] })
    mocks.marketplace.mockResolvedValue({ ok: true, modules: [{
      ...online.modules[0]!, id: 'office-night', name: 'Office Night', content_profile: 'content-pack',
      content_delivery_mode: 'legacy_autoimport', adventure_count: 1,
    }] })
    const { wrapper } = await mountView()

    const panel = await openMyPanel(wrapper)
    const installedRow = panel.get('[data-testid="module-card-office-night"]')
    expect(installedRow.text()).toContain('包含 1 个冒险')
    const onlineCard = wrapper.get('[data-testid="modules-market-office-night"]')
    expect(onlineCard.text()).toContain('1 个冒险')
  })

  it('falls back to the pack id when the catalog has no author and keeps stats behind the Hub flag', async () => {
    mocks.marketplace.mockResolvedValue({ ok: true, modules: [
      { ...online.modules[0]!, author: 'EOEOY', trust_level: 'verified', languages: ['zh-CN', 'en', 'fr'] },
      { ...online.modules[1]!, author: '', license: '' },
    ] })
    const { wrapper } = await mountView()

    const credited = wrapper.get('[data-testid="modules-market-other-module"]')
    expect(credited.get('.cp-author').text()).toBe('EOEOY')
    expect(credited.get('.cp-trust').text()).toBe('已验证作者')
    expect(credited.text()).toContain('2 个冒险')
    expect(credited.find('.cp-stats').exists()).toBe(false)

    const bare = wrapper.get('[data-testid="modules-market-castle-module"]')
    expect(bare.get('.cp-author').text()).toBe('castle-module')
    // 没有 Hub 目录就没有统计与详情入口。
    expect(bare.find('.cp-stats').exists()).toBe(false)
    expect(bare.findAll('button').some(button => button.text() === '查看详情')).toBe(false)
  })

  it('opens the Hub detail dialog with README while keeping the local install state', async () => {
    mocks.marketplace.mockResolvedValue({
      ok: true,
      source: { hub: true },
      modules: online.modules.map(item => item.id === 'castle-module'
        ? { ...item, stats: { downloads_total: 7, rating_average: 4.5, likes: 2 } }
        : item),
    })
    mocks.hubDetail.mockResolvedValue({ ok: true, id: 'castle-module', name: 'Castle Module', version: '1.2.0', plugin_type: 'content-pack', stats: { downloads_total: 7 } })
    mocks.hubReadme.mockResolvedValue({ ok: true, html: '<h1>Castle README</h1>' })
    mocks.hubRatings.mockResolvedValue({ average: 4.5, count: 2, distribution: { 5: 1, 4: 1 } })
    const { wrapper } = await mountView()

    const card = wrapper.get('[data-testid="modules-market-castle-module"]')
    expect(card.get('.cp-stats').text()).toContain('7')
    await card.findAll('button').find(button => button.text() === '查看详情')!.trigger('click')
    await flushPromises()

    expect(mocks.hubDetail).toHaveBeenCalledWith('castle-module', expect.anything())
    expect(mocks.hubReadme).toHaveBeenCalledWith('castle-module', expect.anything())
    // Teleport is stubbed, so the dialog renders inside the view.
    const dialog = wrapper.get('.hub-detail-modal')
    expect(dialog.text()).toContain('内容包详情')
    expect(dialog.text()).toContain('Castle README')
    // The Hub payload has no local install state; the dialog keeps the shelf's
    // (installed 1.0.0, 1.2.0 available), so it offers an update, not an install.
    const action = dialog.get('.hub-detail-hero .n-button')
    expect(action.text()).toBe('更新')

    // Ratings use the same Hub endpoint as plugins.
    mocks.setHubRating.mockResolvedValue({ ok: true })
    await dialog.findAll('.n-rate__item')[3].trigger('click')
    await flushPromises()
    expect(mocks.setHubRating).toHaveBeenCalledWith('castle-module', 4)
  })

  it('says why a pack that needs a newer DiceFrame cannot be installed', async () => {
    mocks.marketplace.mockResolvedValue({ ok: true, modules: [{
      ...online.modules[0]!, installable: false, needs_core_update: true, min_app_version: '3.1.0',
    }] })
    const { wrapper } = await mountView()
    const card = wrapper.get('[data-testid="modules-market-other-module"]')
    expect(card.findAll('button').find(button => button.text() === '安装')!.attributes('disabled')).toBeDefined()
  })

  it('keeps counting a disabled adventure pack by its readable inventory', async () => {
    mocks.list.mockResolvedValue({ ok: true, modules: [{ ...installed.modules[0]!, status: 'disabled', inventory_status: 'disabled' }] })
    const { wrapper } = await mountView()
    const panel = await openMyPanel(wrapper)
    // 禁用包仍按可读清单计数——禁用不是空包。
    expect(panel.text()).toContain('包含 1 个冒险')
  })

  it('treats an unreadable inventory as unknown and remains usable with nullable counts', async () => {
    mocks.list.mockResolvedValue({ ok: true, modules: [{ ...installed.modules[0]!, adventure_count: null, content_counts: null, inventory_status: 'unknown' }] })
    const { wrapper } = await mountView()
    const panel = await openMyPanel(wrapper)
    expect(panel.text()).toContain('内容清单暂不可读')
    expect(panel.text()).not.toContain('包含 0 项资料')
  })

  it('validates the saved digest before offering to resume the game', async () => {
    mocks.api.mockResolvedValue({
      adventure: {
        available: true,
        binding: {
          adventure_id: 'plugin:castle-quest', version: '1.0.0',
          content_digest: 'sha256:other-package', source_kind: 'plugin',
          source_id: 'castle-module',
        },
      },
    })
    const { wrapper } = await mountView({
      adventure: 'plugin:castle-quest', version: '1.0.0',
      digest: 'sha256:deadbeef', source_kind: 'plugin', source_id: 'castle-module',
      game: 'web|room|bot',
    })

    // 同 id、不同 package：服务端可用也不允许"恢复"，绝不自动绑定。
    expect(wrapper.get('[data-testid="modules-recovery"]').text()).toContain('恢复')
    expect(wrapper.find('[data-testid="modules-recovery-blocked"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('当前可用的内容与存档指纹不一致')
    expect(wrapper.find('[data-testid="modules-recovery-ready"]').exists()).toBe(false)
    expect(wrapper.findAll('a').some(link => link.text() === '返回本局')).toBe(false)
  })

  it('offers resume once digest and source match the save', async () => {
    mocks.api.mockResolvedValue({
      adventure: {
        available: true,
        binding: {
          adventure_id: 'plugin:castle-quest', version: '1.0.0',
          content_digest: 'sha256:deadbeef', source_kind: 'plugin',
          source_id: 'castle-module',
        },
      },
    })
    const { wrapper } = await mountView({
      adventure: 'plugin:castle-quest', version: '1.0.0',
      digest: 'sha256:deadbeef', source_kind: 'plugin', source_id: 'castle-module',
      game: 'web|room|bot',
    })

    expect(wrapper.find('[data-testid="modules-recovery-ready"]').exists()).toBe(true)
    const resume = wrapper.findAll('a').find(link => link.text() === '返回本局')
    expect(resume?.attributes('href')).toContain('game=web|room|bot')
  })

  it('shows the server reason when the bound package is missing', async () => {
    mocks.api.mockResolvedValue({
      adventure: {
        available: false,
        reason: 'package_unavailable',
        binding: { adventure_id: 'plugin:castle-quest', version: '1.0.0', content_digest: 'sha256:deadbeef' },
      },
    })
    const { wrapper } = await mountView({
      adventure: 'plugin:castle-quest', version: '1.0.0',
      digest: 'sha256:deadbeef', game: 'web|room|bot',
    })

    expect(wrapper.get('[data-testid="modules-recovery"]').text())
      .toContain('未安装包含该冒险的内容包')
  })

  it('re-validates when the recovery identity changes without a remount', async () => {
    mocks.api.mockResolvedValue({
      adventure: {
        available: true,
        binding: {
          adventure_id: 'plugin:castle-quest', version: '1.0.0',
          content_digest: 'sha256:deadbeef', source_kind: 'plugin', source_id: 'castle-module',
        },
      },
    })
    const { wrapper, router } = await mountView({
      adventure: 'plugin:castle-quest', version: '1.0.0',
      digest: 'sha256:deadbeef', source_kind: 'plugin', source_id: 'castle-module',
      game: 'web|room|bot',
    })
    expect(wrapper.find('[data-testid="modules-recovery-ready"]').exists()).toBe(true)

    // 同页 hash 导航换了一个指纹：必须重新按服务端(不可用/不匹配)判定。
    mocks.api.mockResolvedValue({
      adventure: {
        available: false,
        reason: 'binding_changed',
        binding: { adventure_id: 'plugin:castle-quest', version: '1.0.0', content_digest: 'sha256:deadbeef' },
      },
    })
    await router.push({
      name: 'modules',
      query: {
        adventure: 'plugin:castle-quest', version: '1.0.0',
        digest: 'sha256:other', source_kind: 'plugin', source_id: 'castle-module',
        game: 'web|room|bot',
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="modules-recovery-ready"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="modules-recovery-blocked"]').text()).toContain('已安装的内容包与存档不一致')
  })

  it('shows the required-runtime badge for packs that declare one in their adventures', async () => {
    mocks.list.mockResolvedValue({ ok: true, modules: [{
      ...installed.modules[0]!,
      required_runtimes: [{ id: 'community:fabula-ultima', minimum_version: 1 }],
    }] })
    const { wrapper } = await mountView()
    const panel = await openMyPanel(wrapper)
    expect(panel.get('.modules-my-row-runtime').text()).toContain('需要运行时 community:fabula-ultima ≥1')
  })

  it('labels packs that never declared a language instead of guessing one', async () => {
    mocks.marketplace.mockResolvedValue({
      ok: true,
      modules: [{ ...online.modules[0]!, id: 'no-language', name: 'Undeclared Pack', languages: [] }],
    })
    const { wrapper } = await mountView()
    await wrapper.get('.modules-filter-toggle').trigger('click')
    const languageFilter = wrapper.findAll('[data-testid="modules-market-filters"] select')
      .find(select => select.text().includes('未标注'))
    expect(languageFilter).toBeTruthy()
    await languageFilter!.setValue('__unlabelled__')
    await flushPromises()

    expect(gridCardNames(wrapper)).toEqual(['Undeclared Pack'])
  })

  it('paints a declared cover on installed cards only', async () => {
    mocks.list.mockResolvedValue({
      ok: true,
      modules: [{
        id: 'cover-pack', name: 'Cover Pack', version: '0.1.0',
        content_profile: 'content-pack', content_delivery_mode: 'legacy_autoimport',
        is_module: false, status: 'disabled', adventure_count: 0,
        content_counts: {}, lorebook_count: 0, cover: 'assets/cover.webp',
      }, {
        id: 'plain-pack', name: 'Plain Pack', version: '1.0.0',
        content_profile: 'content-pack', content_delivery_mode: 'legacy_autoimport',
        is_module: false, status: 'disabled', adventure_count: 0,
        content_counts: {}, lorebook_count: 0,
      }],
    })
    const { wrapper } = await mountView()

    const panel = await openMyPanel(wrapper)
    const covered = panel.get('[data-testid="module-card-cover-pack"]')
    expect(covered.get('.modules-my-row-art').attributes('style')).toContain('cover-pack/cover?v=0.1.0')

    const plain = panel.get('[data-testid="module-card-plain-pack"]')
    expect(plain.get('.modules-my-row-art').attributes('style')).toBeUndefined()
  })

  it('flags installed packs that have a newer store version and updates in place', async () => {
    mocks.update.mockResolvedValue({ ok: true, up_to_date: false, version: '1.2.0' })
    const { wrapper, router } = await mountView()
    const panel = await openMyPanel(wrapper)

    // 已安装 + 市场有更新版本 ⇒ 行内有就地更新按钮。
    const row = panel.get('[data-testid="module-card-castle-module"]')
    const updateButton = row.get('.module-update-action')
    expect(updateButton.text()).toContain('更新')
    // 市场里没有更新版本的已安装包不显示提示。
    expect(panel.find('[data-testid="module-card-dormant-module"] .module-update-action').exists()).toBe(false)
    // 顶部「我的内容包」按钮带更新计数。
    expect(wrapper.get('.modules-my-btn').text()).toContain('1')

    await updateButton.trigger('click')
    await flushPromises()

    expect(mocks.update).toHaveBeenCalledWith('castle-module')
    // 行本身是 RouterLink：就地更新不能顺手把用户带进详情页。
    expect(router.currentRoute.value.name).toBe('modules')
    // 更新后必须重读已安装列表与市场索引，版本号才会刷新。
    expect(mocks.list).toHaveBeenCalledTimes(2)
    expect(mocks.marketplace).toHaveBeenCalledTimes(2)
  })

  it('offers one-click update for all installed packs with available updates', async () => {
    mocks.update.mockResolvedValue({ ok: true, up_to_date: false, version: '1.2.0' })
    const { wrapper } = await mountView()
    const panel = await openMyPanel(wrapper)
    const updateAll = panel.get('.module-update-all-action')
    expect(updateAll.text()).toContain('一键更新（1）')

    await updateAll.trigger('click')
    await flushPromises()

    expect(mocks.update).toHaveBeenCalledWith('castle-module')
    wrapper.unmount()
  })
})
