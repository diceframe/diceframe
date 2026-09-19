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
      { path: '/modules/:moduleId', name: 'module-detail', component: emptyView },
      { path: '/play', name: 'play', component: emptyView },
    ],
  })
  await router.push({ name: 'modules', query })
  await router.isReady()
  const wrapper = mount(ModulesView, { global: { plugins: [i18n, router] } })
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

    const installedSection = wrapper.get('[data-testid="modules-installed"]')
    const localSection = wrapper.get('[data-testid="modules-local-import"]')
    const onlineSection = wrapper.get('[data-testid="modules-online"]')

    expect(installedSection.text()).toContain('已安装')
    expect(installedSection.text()).toContain('Castle Module')
    // 禁用中的模组仍然列在"已安装"里（否则无法再启用）。
    expect(installedSection.text()).toContain('Dormant Module')
    expect(installedSection.text()).toContain('已禁用')
    expect(localSection.find('input[type="file"]').exists()).toBe(true)
    expect(onlineSection.text()).toContain('Other Module')
    expect(onlineSection.text()).toContain('更新到 1.2.0')
  })

  it('marks installed online modules and installs missing ones from the marketplace', async () => {
    mocks.installFromMarketplace.mockResolvedValue({ ok: true })
    const { wrapper } = await mountView()

    const fresh = wrapper.get('[data-testid="modules-market-other-module"]')
    const install = fresh.findAll('button').find(button => button.text() === '从市场安装')
    expect(install).toBeDefined()
    await install!.trigger('click')
    await flushPromises()

    expect(mocks.installFromMarketplace).toHaveBeenCalledWith('other-module', false)
    expect(wrapper.get('[data-testid="modules-market-castle-module"]').text()).toContain('更新到 1.2.0')
  })

  it('reports an unreachable marketplace instead of an empty one', async () => {
    mocks.marketplace.mockResolvedValue({
      ok: false, modules: [], error_code: 'MODULE_MARKETPLACE_UNAVAILABLE', error: 'mirror down',
    })
    const { wrapper } = await mountView()

    expect(wrapper.get('[data-testid="modules-online"]').text()).toContain('mirror down')
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
    expect(recovery.text()).toContain('当前可用的模组内容与存档指纹不一致')
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
      .toContain('未安装包含该冒险的模组')
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
