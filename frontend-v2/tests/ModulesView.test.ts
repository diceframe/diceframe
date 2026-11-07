import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { i18n } from '../src/i18n'
import ModulesView from '../src/features/modules/ModulesView.vue'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  list: vi.fn(),
  marketplace: vi.fn(),
  previewImport: vi.fn(),
  importModule: vi.fn(),
  installFromMarketplace: vi.fn(),
}))

vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api }
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
    languages: ['zh-CN'], tags: ['adventure'], trust_level: 'community',
    distribution: 'repository', repository_url: '', release_url: '', stars: 0,
    installed: false, installed_version: '', update_available: false,
    installable: true, verification_error: '', needs_core_update: false, min_app_version: '',
  }, {
    id: 'castle-module', name: 'Castle Module', version: '1.0.0', latest_version: '1.2.0',
    description: '', content_profile: 'adventure-module', content_delivery_mode: 'catalog',
    adventure_count: 1, ruleset_targets: ['core:dnd2024'], languages: ['zh-CN'],
    tags: [], trust_level: 'official', distribution: 'repository', repository_url: '',
    release_url: '', stars: 3, installed: true, installed_version: '1.0.0',
    update_available: true, installable: true, verification_error: '',
    needs_core_update: false, min_app_version: '',
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

describe('ModulesView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18n.global.locale.value = 'zh-CN'
    mocks.list.mockResolvedValue(installed)
    mocks.marketplace.mockResolvedValue(online)
    mocks.api.mockResolvedValue({ adventure: null })
  })

  it('separates installed, local import, and online modules', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.get('h1').text()).toBe('内容包')
    expect(wrapper.get('.view-intro').text()).toContain('安装和更新内容包，查看资源与版本。')

    const installedSection = wrapper.get('[data-testid="modules-installed"]')
    const localSection = wrapper.get('[data-testid="modules-local-import"]')
    const onlineSection = wrapper.get('[data-testid="modules-online"]')

    expect(installedSection.text()).toContain('已安装')
    expect(installedSection.text()).toContain('Castle Module')
    // 禁用中的模组仍然列在"已安装"里（否则无法再启用）。
    expect(installedSection.text()).toContain('Dormant Module')
    expect(installedSection.text()).toContain('已禁用')
    expect(installedSection.text()).toContain('冒险模组')
    expect(installedSection.text()).toContain('资源包')
    expect(installedSection.text()).toContain('不含固定剧情')
    expect(localSection.find('input[type="file"]').exists()).toBe(true)
    expect(onlineSection.text()).toContain('Other Module')
    expect(onlineSection.text()).toContain('包含 2 个冒险')
    expect(onlineSection.text()).toContain('更新到 1.2.0')
    expect(wrapper.find('a[href="#modules-installed"]').exists()).toBe(false)
    expect(wrapper.find('a[href="#modules-local-import"]').exists()).toBe(false)
    expect(wrapper.find('a[href="#modules-online"]').exists()).toBe(false)
  })

  it('keeps the return-to-library action after removing duplicate section links', async () => {
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
    const refresh = wrapper.findAll('button').find(button => button.text() === '刷新')
    expect(refresh).toBeDefined()
    mocks.list.mockResolvedValueOnce(installed)
    await refresh!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="modules-installed"]').text()).toContain('Castle Module')
  })

  it('marks installed online modules and installs missing ones from the marketplace', async () => {
    mocks.installFromMarketplace.mockResolvedValue({ ok: true })
    const { wrapper } = await mountView()

    const fresh = wrapper.get('[data-testid="modules-market-other-module"]')
    const install = fresh.findAll('button').find(button => button.text() === '从市场安装')
    expect(install).toBeDefined()
    await install!.trigger('click')
    await flushPromises()
    await vi.waitFor(() => expect(fresh.get('[role="status"]').text()).toMatch(/已安装，(正在刷新列表|列表已刷新)/))

    expect(mocks.installFromMarketplace).toHaveBeenCalledWith('other-module', false)
    expect(wrapper.get('[data-testid="modules-market-castle-module"]').text()).toContain('更新到 1.2.0')
  })

  it('shows the marketplace error when the install endpoint returns a failed result', async () => {
    mocks.installFromMarketplace.mockResolvedValue({ ok: false, error: '签名校验失败' })
    const { wrapper } = await mountView()

    const fresh = wrapper.get('[data-testid="modules-market-other-module"]')
    await fresh.findAll('button').find(button => button.text() === '从市场安装')!.trigger('click')
    await flushPromises()

    expect(fresh.get('[role="status"]').text()).toContain('签名校验失败')
    expect(fresh.get('[role="status"]').classes()).toContain('is-error')
  })

  it('reports an unreachable marketplace instead of an empty one', async () => {
    mocks.marketplace.mockResolvedValue({
      ok: false, modules: [], error_code: 'MODULE_MARKETPLACE_UNAVAILABLE', error: 'mirror down',
    })
    const { wrapper } = await mountView()

    expect(wrapper.get('[data-testid="modules-online"]').text()).toContain('mirror down')
  })

  it('explains how packs get listed with a Workshop exit and a direct submission exit', async () => {
    const { wrapper } = await mountView()
    expect(wrapper.find('[data-testid="modules-submit-guide"]').exists()).toBe(false)

    await wrapper.get('[data-testid="modules-submit-guide-open"]').trigger('click')
    const guide = wrapper.get('[data-testid="modules-submit-guide"]')
    expect(guide.findAll('li')).toHaveLength(3)
    expect(guide.text()).toContain('在创作工坊导出冒险模组或资源包')
    expect(guide.get('[data-testid="modules-submit-go-creator"]').attributes('href')).toBe('/creator')
    const direct = guide.get('[data-testid="modules-submit-direct"]')
    expect([direct.attributes('target'), direct.attributes('rel')]).toEqual(['_blank', 'noopener'])
    const url = new URL(direct.attributes('href') || '')
    expect(`${url.origin}${url.pathname}`).toBe('https://github.com/diceframe/diceframe-plugins/issues/new')
    expect(Object.fromEntries(url.searchParams)).toEqual({ template: 'add-content-pack.yml' })
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
    expect(card.text()).toContain('不含固定剧情')
    expect(card.text()).not.toContain('包含 0 项资料')
  })

  it('keeps a disabled installed adventure classified by its readable inventory', async () => {
    mocks.list.mockResolvedValue({ ok: true, modules: [{ ...installed.modules[0], status: 'disabled', inventory_status: 'disabled' }] })
    const { wrapper } = await mountView()
    const section = wrapper.get('[data-testid="modules-installed"]')
    expect(section.text()).toContain('冒险模组')
    expect(section.text()).toContain('启用后')
    expect(section.text()).not.toContain('不含固定剧情')
  })

  it('treats an unreadable inventory as unknown and remains usable with nullable counts', async () => {
    mocks.list.mockResolvedValue({ ok: true, modules: [{ ...installed.modules[0], adventure_count: null, content_counts: null, inventory_status: 'unknown' }] })
    const { wrapper } = await mountView()
    const section = wrapper.get('[data-testid="modules-installed"]')
    expect(section.text()).toContain('内容清单暂不可读')
    expect(section.text()).not.toContain('不含固定剧情')
    expect(section.text()).not.toContain('包含 0 项资料')
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
    const recovery = wrapper.get('[data-testid="modules-recovery"]')
    expect(wrapper.find('[data-testid="modules-recovery-blocked"]').exists()).toBe(true)
    expect(recovery.text()).toContain('当前可用的内容与存档指纹不一致')
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
    expect(resume?.attributes('href')).toContain('web|room|bot')
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
    expect(wrapper.get('[data-testid="modules-recovery-blocked"]').text()).toContain('存档不一致')
  })
})
