import { mount } from '@vue/test-utils'
import { effectScope, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import type { PluginMarketplaceItem } from '../src/api/types'

vi.mock('../src/api/plugins', () => ({ pluginApi: {} }))
vi.mock('../src/composables/useToast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }) }))
vi.mock('../src/composables/useConfirm', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
// useLocale needs a component setup context; route it to the global i18n instance.
vi.mock('../src/composables/useLocale', async () => {
  const { i18n: global } = await import('../src/i18n')
  return { useLocale: () => ({ t: (key: string, values?: Record<string, unknown>) => global.global.t(key as never, values as never), locale: global.global.locale }) }
})

import { usePluginMarketplace } from '../src/features/plugins/usePluginMarketplace'
import MarketplaceTab from '../src/features/plugins/tabs/MarketplaceTab.vue'

function item(id: string, pluginType: string): PluginMarketplaceItem {
  return { id, name: id, plugin_type: pluginType } as PluginMarketplaceItem
}

describe('plugin market does not list content packs', () => {
  it('filters content-pack out of the plugin market regardless of the type filter', () => {
    const scope = effectScope()
    const typeFilter = ref('')
    const market = scope.run(() => usePluginMarketplace(ref(''), async () => {}, typeFilter))!
    market.marketplace.value = [item('office-night', 'content-pack'), item('qq-napcat', 'channel-adapter'), item('tunnel', 'tool')]
    expect(market.filteredMarketplace.value.map(entry => entry.id)).toEqual(['qq-napcat', 'tunnel'])
    // Even a stale "content-pack" filter cannot bring content packs back here.
    typeFilter.value = 'content-pack'
    expect(market.filteredMarketplace.value).toEqual([])
    scope.stop()
  })

  it('points content packs to the module library instead of a second store tab', async () => {
    i18n.global.locale.value = 'zh-CN'
    const router = createRouter({
      history: createMemoryHistory(),
      routes: ['plugins', 'modules'].map(name => ({ path: `/${name}`, name, component: { template: '<div />' } })),
    })
    await router.push('/plugins')
    const wrapper = mount(MarketplaceTab, {
      global: { plugins: [i18n, router] },
      props: {
        marketKeyword: '', sortMode: '', marketLoading: false, marketplaceSource: null,
        filteredMarketplace: [], paginatedMarketplace: [], totalPages: 1, page: 1, typeFilter: '',
        pluginTypeFilters: [{ value: 'tool', labelKey: 'pluginTypeTool' }], sortOptions: [], busy: '',
        pluginTypeIcon: () => ({ render: () => null }), pluginTypeLabel: (type?: string) => String(type || ''),
        marketItemHasNewerVersion: () => false, loadMarketplace: () => {}, installMarketPlugin: () => {},
        openUrl: () => {}, openHubDetail: () => {}, goToPage: () => {},
      },
    })
    const hint = wrapper.get('[data-testid="plugin-market-content-hint"]')
    expect(hint.text()).toContain('冒险模组和资源包统一在内容包页面的「在线内容包」里安装。')
    expect(hint.get('a').attributes('href')).toBe('/modules')
    expect(wrapper.find('.mode-tabs').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('内容市场')
  })
})
