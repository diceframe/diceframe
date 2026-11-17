import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import ContentLibraryView from '../src/features/library/ContentLibraryView.vue'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../src/api/client', () => ({ api: mocks.api, errorMessage: (error: unknown) => String(error) }))

const adventure = (sourceId = '') => ({
  adventure_id: 'shared:story', name: '雾港失踪案', summary: '调查失踪的灯塔看守。', version: '1.2.0',
  source_kind: sourceId ? 'plugin' : 'builtin', source_id: sourceId, format: 'diceframe:adventure-graph-v2',
  required_runtime: { id: 'core:dnd2024', minimum_version: 1 }, world_policy: 'portable', recommended_world_id: '',
  compatibility: 'incompatible', incompatibility_reasons: ['ruleset_runtime_unavailable'],
})
const rules = [{ rule_id: 'dnd2024_srd', rule_name: 'D&D 2024', ruleset_runtime: {
  id: 'core:dnd2024', version: 1, capabilities: { adventure_formats: ['diceframe:adventure-graph-v2'] },
} }]
const mounted: Array<ReturnType<typeof mount>> = []

async function render() {
  const empty = defineComponent({ template: '<div />' })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/library', name: 'library', component: ContentLibraryView },
    ...['create', 'worlds', 'modules', 'adventures', 'rules', 'creator', 'lorebook', 'module-detail'].map(name => ({ path: `/${name}`, name, component: empty })),
  ] })
  await router.push('/library')
  const wrapper = mount(ContentLibraryView, { attachTo: document.body, global: { plugins: [i18n, router] } })
  mounted.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  mocks.api.mockReset()
  i18n.global.locale.value = 'zh-CN'
  mocks.api.mockImplementation(async (path: string) => path.startsWith('/rules')
    ? { rules }
    : { ok: true, adventures: [adventure(), adventure('installed:one')] })
})
afterEach(() => { mounted.splice(0).forEach(wrapper => wrapper.unmount()); document.body.innerHTML = '' })

describe('player content library', () => {
  it('distinguishes a world primary book from its world and preserves custom book titles and identity', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/worlds') return { worlds: [{ id: 'port', name: '港湾世界', language: 'zh-CN' }] }
      if (path === '/lorebooks?scope=all') return { books: [
        { id: 'primary:port', name: '港湾世界', primary: true, language: 'zh-CN', bindings: [{ scope_kind: 'world', scope_id: 'port', role: 'primary' }] },
        { id: 'book:people', name: '港湾人物志', language: 'zh-CN', bindings: [{ scope_kind: 'world', scope_id: 'port' }] },
        { id: 'independent', name: '通用装备手册', language: 'zh-CN', bindings: [] },
      ] }
      return { ok: true }
    })
    const { wrapper, router } = await render()
    const cards = wrapper.findAll('[data-testid="library-lorebook"]')
    expect(cards[0]!.get('h3').text()).toBe('港湾世界 · 主资料册')
    expect(cards[0]!.text()).toContain('所属世界：港湾世界')
    expect(cards[1]!.get('h3').text()).toBe('港湾人物志')
    expect(cards[2]!.text()).toContain('独立资料册')
    await cards[1]!.get('a').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ world: 'port', book: 'book:people' })
    expect(mocks.api.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
  })

  it('keeps same-id adventures from separate sources and hands the exact package to creation', async () => {
    const { wrapper, router } = await render()
    const cards = wrapper.findAll('[data-testid="library-adventure"]')
    expect(cards).toHaveLength(2)
    expect(wrapper.get('h1').text()).toBe('内容库')
    expect(wrapper.get('.section-kicker').text()).toBe('CONTENT LIBRARY')
    expect(cards[0]!.get('img').attributes('src')).toBe('/v2-assets/ui/campaign-moonlit-ruins.jpg')
    expect(cards[0]!.text()).toContain('内置')
    expect(cards[1]!.text()).toContain('installed:one')
    // Discovery has no selected ruleset/world. It must not mistake that context
    // for a permanent incompatibility or bypass CreateView's final validation.
    await cards[1]!.get('[data-testid="library-start"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('create')
    expect(router.currentRoute.value.query).toEqual({ adventure_id: 'shared:story', module_id: 'installed:one', return_to: 'library' })
    expect(mocks.api.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
  })

  it('filters by canonical source and supports clearing a search without losing entries', async () => {
    const { wrapper } = await render()
    await wrapper.get('[data-testid="library-source-filter"]').setValue('plugin')
    expect(wrapper.findAll('[data-testid="library-adventure"]')).toHaveLength(1)
    await wrapper.get('[data-testid="library-search"]').setValue('找不到的内容')
    expect(wrapper.text()).toContain('没有匹配项')
    await wrapper.get('[data-testid="library-clear"]').trigger('click')
    expect(wrapper.findAll('[data-testid="library-adventure"]')).toHaveLength(2)
  })

  it('keeps a type filter scoped to its owning content section', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/rules')) return { rules }
      if (path === '/worlds') return { worlds: [{ id: 'world-1', name: '港湾世界', entry_count: 1 }] }
      if (path === '/lorebooks?scope=all') return { books: [{ id: 'book-1', name: '港湾资料册', entry_count: 2 }] }
      if (path === '/modules') return { ok: true, modules: [{ id: 'pack-1', name: '内容包', content_profile: 'content-pack', content_counts: { worlds: 1 }, adventure_count: 0, status: 'enabled', inventory_status: 'ready' }] }
      return { ok: true, adventures: [adventure()] }
    })
    const { wrapper } = await render()
    await wrapper.get('[data-testid="library-tab-lorebook"]').trigger('click')
    expect(wrapper.findAll('[data-testid="library-lorebook"]')).toHaveLength(1)
    expect(wrapper.find('[data-testid="library-adventure"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="library-world"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="library-source"]').exists()).toBe(false)
  })

  it('does not show an executable start when no installed rule declares the required format', async () => {
    mocks.api.mockImplementation(async (path: string) => path.startsWith('/rules') ? { rules: [] } : { ok: true, adventures: [adventure()] })
    const { wrapper } = await render()
    expect(wrapper.find('[data-testid="library-start"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('不可用')
    await wrapper.get('[data-testid="library-details"]').trigger('click')
    await flushPromises()
    const dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('调查失踪的灯塔看守')
    expect(dialog.textContent).toContain('core:dnd2024')
    expect(mocks.api.mock.calls.some(([path]) => /\/adventures\//.test(path))).toBe(false)
  })

  it('surfaces catalogue failure and retries rather than presenting a false empty library', async () => {
    mocks.api.mockImplementation(async (path: string) => path.startsWith('/rules') ? { rules } : { ok: false, error: 'catalogue offline' })
    const { wrapper } = await render()
    expect(wrapper.get('[role="alert"]').text()).toContain('catalogue offline')
    expect(wrapper.find('[data-testid="library-empty"]').exists()).toBe(false)
    mocks.api.mockImplementation(async (path: string) => path.startsWith('/rules') ? { rules } : { ok: true, adventures: [adventure()] })
    await wrapper.get('[data-testid="library-retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-testid="library-adventure"]')).toHaveLength(1)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('ignores a late catalogue response after the content language changes', async () => {
    let oldResolve!: (value: unknown) => void
    mocks.api.mockImplementation((path: string) => {
      if (path.startsWith('/rules')) return Promise.resolve({ rules })
      if (path.includes('language=zh-CN')) return new Promise(resolve => { oldResolve = resolve })
      return Promise.resolve({ ok: true, adventures: [{ ...adventure(), name: 'New catalogue' }] })
    })
    const { wrapper } = await render()
    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(wrapper.text()).toContain('New catalogue')
    oldResolve({ ok: true, adventures: [{ ...adventure(), name: 'Old catalogue' }] })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Old catalogue')
    expect(wrapper.get('h1').text()).toBe('Content library')
  })

  it('keeps creation and review tools on their owning adventures route', async () => {
    const { wrapper, router } = await render()
    const links = wrapper.findAll('.library-creation-tools a')
    expect(links).toHaveLength(4)
    await links[0]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('adventures')
    expect(router.currentRoute.value.query).toEqual({})
  })

  it('renders world cards with the same compact knowledge-card layout as lorebooks', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/rules')) return { rules }
      if (path === '/worlds') return { worlds: [{ id: 'world-1', name: '港湾世界', description: '潮湿的海港。', entry_count: 3 }] }
      if (path.startsWith('/world-templates')) return { templates: [{ world_id: 'world-1', scene_image: { kind: 'builtin', id: 'freeform_fantasy' }, default_rule: 'freeform_fantasy' }] }
      if (path === '/lorebooks?scope=all') return { books: [] }
      if (path === '/modules') return { ok: true, modules: [] }
      return { ok: true, adventures: [] }
    })
    const { wrapper } = await render()
    const card = wrapper.get('[data-testid="library-world"]')
    expect(card.classes()).toContain('hub-world-card')
    expect(card.classes()).not.toContain('world-card')
    expect(card.find('.hub-card-cover').exists()).toBe(false)
    expect(card.find('.hub-card-content').exists()).toBe(true)
    expect(card.find('.hub-card-actions').exists()).toBe(true)
    expect(card.findAll('a').some(link => link.text() === '编辑世界' && link.attributes('href')?.includes('edit=1'))).toBe(true)
  })

  it('filters world cards by the selected content language', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/rules')) return { rules }
      if (path === '/worlds') return { worlds: [
        { id: 'world-zh', name: '中文世界', language: 'zh-CN', entry_count: 1 },
        { id: 'world-en', name: 'English world', language: 'en', entry_count: 1 },
      ] }
      if (path.startsWith('/world-templates')) return { templates: [] }
      if (path === '/lorebooks?scope=all') return { books: [] }
      if (path === '/modules') return { ok: true, modules: [] }
      return { ok: true, adventures: [] }
    })
    const { wrapper } = await render()
    expect(wrapper.text()).toContain('中文世界')
    expect(wrapper.text()).not.toContain('English world')
    await wrapper.get('[data-testid="library-language-filter"]').setValue('en')
    expect(wrapper.text()).toContain('English world')
    expect(wrapper.text()).not.toContain('中文世界')
  })

  it('opens an orphaned world-sourced book by book id without inventing a missing world query', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/rules')) return { rules }
      if (path === '/worlds') return { worlds: [] }
      if (path.startsWith('/world-templates')) return { templates: [] }
      if (path === '/lorebooks?scope=all') return {
        books: [{ id: 'world:missing-world', name: '孤立资料册', entry_count: 2, bindings: [{ scope_kind: 'world', scope_id: 'missing-world', role: 'primary' }], ref: { kind: 'lorebook', source_kind: 'world', source_id: 'missing-world' } }],
      }
      if (path === '/modules') return { ok: true, modules: [] }
      return { ok: true, adventures: [] }
    })
    const { wrapper, router } = await render()
    const link = wrapper.get('[data-testid="library-lorebook"] a[data-book-id="world:missing-world"]')
    expect(wrapper.get('[data-testid="library-lorebook"]').text()).not.toContain('missing-world')
    expect(wrapper.get('[data-testid="library-lorebook"]').text()).toContain('所属世界：未列入世界库')
    expect(new URL(link.attributes('href') || '', 'http://localhost').searchParams.get('book')).toBe('world:missing-world')
    expect(link.attributes('href')).not.toContain('world=missing-world')
    await link.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ book: 'world:missing-world' })
  })
})
