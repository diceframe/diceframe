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
  if (path === '/worlds') return { worlds: [] }
  if (path.startsWith('/lorebooks?')) return { books: [] }
  if (path === '/character-cards') return { cards: [] }
  if (path.startsWith('/adventures')) return { ok: true, adventures: [
    {
      adventure_id: 'dagon-1', source_kind: 'plugin', source_id: MODULE_ID, version: '1.0.0',
      format: 'diceframe:adventure-graph-v2', world_policy: 'portable', recommended_world_id: 'greymoor',
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

  it('clears module book candidates when the module source is left', async () => {
    const { wrapper, vm } = await mountWithModuleAdventure()
    expect(vm.moduleLorebookRows.length).toBe(1)

    vm.moduleId = ''
    await flushPromises()
    expect(vm.moduleLorebookRows.length).toBe(0)
    expect(vm.availableLorebooks.some((book: any) => book.id === BOOK_ID)).toBe(false)
    wrapper.unmount()
  })
})
