import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CreateView from '../src/features/create/CreateView.vue'
import { i18n } from '../src/i18n'
import { clearAllCreateDraftsForTests, setCreateDraftSession } from '../src/features/create/createDraft'

const MODULE_ID = 'lore-pack'
const BOOK_ID = 'module_book_lore_pack_dagon_archive'

const mocks = vi.hoisted(() => ({
  route: { query: {} as Record<string, string> },
  router: { push: vi.fn(), resolve: vi.fn(() => ({ href: '/adventures' })) },
  apiCalls: [] as Array<{ path: string; init?: RequestInit }>,
  books: [] as unknown[],
  worlds: [] as unknown[],
  recommendedWorld: 'greymoor',
}))

vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => mocks.router }))
vi.mock('../src/stores/useSettingsStore', () => ({ useSettingsStore: () => ({
  config: { llm_provider_ref: 'p1', ai_providers: [{ id: 'p1', base_url: 'http://llm.test' }], model: 'm1' },
  error: '',
  load: vi.fn().mockResolvedValue(undefined),
}) }))
vi.mock('../src/composables/useToast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }) }))
vi.mock('../src/api/sceneImages', () => ({
  resolveSceneImageUrl: vi.fn().mockResolvedValue(''),
  revokeSceneImageUrl: vi.fn(),
  uploadSceneImage: vi.fn(),
  sceneImageStyle: vi.fn(() => ({})),
  sceneImageCandidates: vi.fn((primary: string, variants: string[] = []) => primary ? [primary, ...variants] : variants),
  pickSceneImageReference: vi.fn((primary: string, variants: string[] = []) => primary || variants[0]),
}))
vi.mock('../src/api/mapBackgrounds', () => ({ mapBackgroundSelection: vi.fn(() => null), uploadMapBackground: vi.fn() }))
vi.mock('../src/api/client', () => ({ api: vi.fn(async (path: string, init?: RequestInit) => {
  mocks.apiCalls.push({ path, init })
  if (path.startsWith('/world-templates')) return { templates: [{ world_id: 'greymoor', world_name: '灰沼', default_rule: 'freeform_srd' }] }
  if (path.startsWith('/rules?')) return { rules: [
    {
      rule_id: 'freeform_srd', rule_name: 'Freeform',
      ruleset_runtime: { id: 'core:freeform', version: 1, capabilities: { adventure_formats: ['diceframe:adventure-graph-v2'] } },
    },
  ] }
  if (path === '/worlds') return { worlds: mocks.worlds }
  if (path.startsWith('/lorebooks?')) return { books: mocks.books }
  if (path === '/character-cards') return { cards: [] }
  if (path.startsWith('/adventures')) return { ok: true, adventures: [
    {
      adventure_id: 'dagon-1', source_kind: 'plugin', source_id: MODULE_ID, version: '1.0.0',
      format: 'diceframe:adventure-graph-v2', world_policy: 'portable', recommended_world_id: mocks.recommendedWorld,
      required_runtime: { id: 'core:adventure', minimum_version: 1 },
      name: '雾港疑云', summary: 'CoC 模组', estimated_minutes: 90,
      compatibility: 'compatible', incompatibility_reasons: [],
    },
  ] }
  if (path.startsWith(`/modules/${MODULE_ID}/lorebooks`)) return {
    ok: true,
    lorebooks: [{
      id: BOOK_ID, name: 'Dagon Archive', description: '守秘人档案', language: 'zh-CN',
      enabled: true, source_kind: 'module', source_id: MODULE_ID, catalog: true, entry_count: 27,
      ref: { source_kind: 'module', source_id: MODULE_ID, kind: 'lorebook', id: BOOK_ID, digest: '' },
    }],
  }
  if (path === '/games/create') return { ok: true, game_key: 'g1' }
  return { ok: true }
}), errorMessage: (error: unknown) => String(error), apiBlob: vi.fn() }))
vi.mock('../src/api/modules', () => ({ moduleApi: { list: vi.fn().mockResolvedValue({ modules: [] }) } }))

const stubs = {
  CharacterWizard: true, CharacterCardPicker: true, PortraitImage: true, AdventureSceneImagePicker: true,
  MapBackgroundPicker: true, RulesetExperienceHost: true, AdventureLaunchSummary: true,
}

function postedBody(path: string): Record<string, unknown> | null {
  const call = mocks.apiCalls.find(item => item.path === path && item.init?.method === 'POST')
  if (!call?.init?.body) return null
  return JSON.parse(String(call.init.body)) as Record<string, unknown>
}

describe('CreateView module lorebook landing (issue #700)', () => {
  afterEach(() => {
    clearAllCreateDraftsForTests()
    setCreateDraftSession(null)
    mocks.router.push.mockReset()
    mocks.route.query = {}
    mocks.apiCalls.length = 0
    mocks.books = []
    mocks.worlds = []
    mocks.recommendedWorld = 'greymoor'
  })

  async function mountWithModuleAdventure() {
    i18n.global.locale.value = 'zh-CN'
    mocks.route.query = { module_id: MODULE_ID, adventure_id: 'dagon-1' }
    const wrapper = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.moduleId).toBe(MODULE_ID)
    expect(vm.playMode).toBe('adventure')
    return { wrapper, vm }
  }

  it('offers the module-declared book as a launch candidate and binds it in the create payload', async () => {
    const { wrapper, vm } = await mountWithModuleAdventure()

    // 模块详情接口的声明书进入候选（默认勾选，可取消）。
    expect(vm.moduleLorebookRows.length).toBe(1)
    expect(vm.availableLorebooks.some((book: any) => book.id === BOOK_ID && book.ref?.source_kind === 'module')).toBe(true)
    expect(vm.selectedBookIds).toContain(BOOK_ID)

    const bookRows = wrapper.findAll('details.create-lorebook-summary ul li')
    expect(bookRows.some(row => row.text().includes('Dagon Archive'))).toBe(true)

    // 真实创建 payload：module 来源的 book binding，服务端按需物化。
    vm.step = 4
    vm.characters = [{ character_name: '艾琳', identity: {}, attributes: {}, skills: [] }]
    await vm.create()
    await flushPromises()
    const body = postedBody('/games/create')
    expect(body).not.toBeNull()
    const bindings = (body?.book_bindings || []) as Array<Record<string, unknown>>
    expect(bindings.length).toBeGreaterThan(0)
    expect(bindings[0]!.ref).toMatchObject({ source_kind: 'module', source_id: MODULE_ID, kind: 'lorebook', id: BOOK_ID })
    wrapper.unmount()
  })

  it('keeps an explicit deselection of the module book out of the payload', async () => {
    const { wrapper, vm } = await mountWithModuleAdventure()
    expect(vm.selectedBookIds).toContain(BOOK_ID)

    const checkbox = wrapper.find('details.create-lorebook-summary ul li input[type="checkbox"]')
    await checkbox.setValue(false)
    expect(vm.selectedBookIds).not.toContain(BOOK_ID)

    vm.step = 4
    vm.characters = [{ character_name: '艾琳', identity: {}, attributes: {}, skills: [] }]
    await vm.create()
    await flushPromises()
    const body = postedBody('/games/create')
    const bindings = (body?.book_bindings || []) as Array<Record<string, unknown>>
    expect(bindings).toEqual([])
    wrapper.unmount()
  })

  it('separates the empty world main book from the pack book that holds the entries', async () => {
    // 灰砧镇：世界主册自动创建、0 条；18 条全在包内资料册里。
    mocks.books = [{
      id: 'world:greymoor', name: '灰沼基础资料', enabled: true, entry_count: 0, source_kind: 'world',
      bindings: [{ scope_kind: 'world', scope_id: 'greymoor', role: 'primary' }],
    }]
    mocks.worlds = [{ id: 'greymoor', name: '灰沼' }]
    const { wrapper, vm } = await mountWithModuleAdventure()

    const rows = wrapper.findAll('details.create-lorebook-summary ul li')
    expect(rows).toHaveLength(2)
    // 空白主册是容器：有说明、没有勾选框，也不计入"已选"。
    expect(rows[0]!.text()).toContain('暂无条目')
    expect(rows[0]!.find('input[type="checkbox"]').exists()).toBe(false)
    expect(rows[1]!.text()).toContain('内容包')
    expect(rows[1]!.text()).toContain('27 条')
    expect(wrapper.findAll('details.create-lorebook-summary ul li input[type="checkbox"]')).toHaveLength(1)
    expect(wrapper.get('details.create-lorebook-summary summary').text()).toContain('已选 1 册')

    // 世界自己能编辑，但它一本书的内容都没有：链接指向真正有内容的包页，
    // 而不是一个空编辑器。主册仍随世界带入，binding 语义不变。
    expect(vm.loreManageTarget).toEqual({ kind: 'module', moduleId: MODULE_ID })
    expect(vm.selectedBookIds).toEqual(['world:greymoor', BOOK_ID])
    wrapper.unmount()
  })

  it('builds the world from a portable module instead of borrowing an unrelated template world', async () => {
    // 大衮：manifest 声明了 recommended_world_id，但包里没有这个世界模板。
    // 这时不能顺手用下拉框里的模板世界——那会把别人世界的资料册整本带进本局。
    mocks.recommendedWorld = 'pacific_shoal'
    mocks.books = [{
      id: 'world:greymoor', name: '灰沼基础资料', enabled: true, entry_count: 5, source_kind: 'world',
      bindings: [{ scope_kind: 'world', scope_id: 'greymoor', role: 'primary' }],
    }]
    mocks.worlds = [{ id: 'greymoor', name: '灰沼' }]
    const { wrapper, vm } = await mountWithModuleAdventure()

    expect(vm.moduleWorldApplies).toBe(true)
    expect(wrapper.get('[data-testid="create-module-world"]').text()).toContain('雾港疑云')
    // 模板世界不再出现在本局里：没有它的下拉项，也没有它的资料册行。
    expect(wrapper.findAll('select').some(select => select.text().includes('灰沼'))).toBe(false)
    const rows = wrapper.findAll('details.create-lorebook-summary ul li')
    expect(rows).toHaveLength(1)
    expect(rows[0]!.text()).toContain('Dagon Archive')
    expect(rows[0]!.text()).not.toContain('灰沼基础资料')

    vm.step = 4
    vm.characters = [{ character_name: '艾琳', identity: {}, attributes: {}, skills: [] }]
    await vm.create()
    await flushPromises()
    const body = postedBody('/games/create')
    expect(body?.world_id).toBe('module_lore-pack_dagon-1')
    expect(body?.custom_world).toBe(true)
    expect(body?.world_name).toBe('雾港疑云')
    wrapper.unmount()
  })

  it('carries the pack into free play for resource-only packs (module_id without adventure_id)', async () => {    i18n.global.locale.value = 'zh-CN'
    mocks.route.query = { module_id: MODULE_ID }
    const wrapper = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const vm = wrapper.vm as any

    expect(vm.moduleId).toBe(MODULE_ID)
    expect(vm.playMode).toBe('free')
    expect(vm.moduleLorebookRows.length).toBe(1)
    expect(vm.selectedBookIds).toContain(BOOK_ID)
    wrapper.unmount()
  })

  it('clears module book candidates when the module source is left', async () => {    const { wrapper, vm } = await mountWithModuleAdventure()
    expect(vm.moduleLorebookRows.length).toBe(1)

    vm.moduleId = ''
    await flushPromises()
    expect(vm.moduleLorebookRows.length).toBe(0)
    expect(vm.availableLorebooks.some((book: any) => book.id === BOOK_ID)).toBe(false)
    wrapper.unmount()
  })
})
