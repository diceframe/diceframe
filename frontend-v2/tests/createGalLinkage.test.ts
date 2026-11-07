import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CreateView from '../src/features/create/CreateView.vue'
import { i18n } from '../src/i18n'
import { clearAllCreateDraftsForTests, setCreateDraftSession } from '../src/features/create/createDraft'

const mocks = vi.hoisted(() => ({
  route: { query: {} as Record<string, string> },
  router: { push: vi.fn(), resolve: vi.fn(() => ({ href: '/adventures' })) },
  apiCalls: [] as Array<{ path: string; init?: RequestInit }>,
}))

vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => mocks.router }))
// LLM 配置就绪，create() 才不会被 requireApiConfiguration 拦下，能走到真实 POST。
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
  sceneImageCandidates: vi.fn((primary, variants = []) => primary ? [primary, ...variants] : variants),
  pickSceneImageReference: vi.fn((primary, variants = []) => primary || variants[0]),
}))
vi.mock('../src/api/mapBackgrounds', () => ({ mapBackgroundSelection: vi.fn(() => null), uploadMapBackground: vi.fn() }))
vi.mock('../src/api/client', () => ({ api: vi.fn(async (path: string, init?: RequestInit) => {
  mocks.apiCalls.push({ path, init })
  if (path.startsWith('/world-templates')) return { templates: [{ world_id: 'greymoor', world_name: '灰沼', default_rule: 'freeform_srd' }] }
  if (path.startsWith('/rules?')) return { rules: [
    { rule_id: 'freeform_srd', rule_name: 'Freeform' },
    { rule_id: 'gal_narrative', rule_name: 'GAL Narrative Preview' },
  ] }
  if (path === '/worlds') return { worlds: [] }
  if (path.startsWith('/lorebooks?')) return { books: [] }
  if (path === '/character-cards') return { cards: [] }
  if (path === '/generate-world') return { ok: true, world_id: 'ai-world-1', world_name: 'AI 世界' }
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

describe('CreateView GAL source-switch consistency', () => {
  afterEach(() => {
    clearAllCreateDraftsForTests()
    setCreateDraftSession(null)
    mocks.router.push.mockReset()
    mocks.route.query = {}
    mocks.apiCalls.length = 0
  })

  async function mountWithGalaRuleSelected() {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.playMode).toBe('free')
    // 模板来源选择 GAL 规则：play_mode/solo 立即联动成合法组合
    await wrapper.get('details.create-rule-settings select').setValue('gal_narrative')
    await flushPromises()
    expect(vm.activeRule).toBe('gal_narrative')
    expect(vm.playMode).toBe('gal')
    expect(vm.solo).toBe(true)
    return { wrapper, vm }
  }

  it('switching from template GAL to the AI source restores a legal play mode and POST payload', async () => {
    const { wrapper, vm } = await mountWithGalaRuleSelected()
    // 切到 AI 世界来源：生效规则变为非 GAL 的基础规则，gal play_mode 必须降级
    await wrapper.findAll('.create-world-source .create-mode-cards button')[2].trigger('click')
    await flushPromises()
    expect(vm.activeRule).toBe('freeform_srd')
    expect(vm.playMode).toBe('free')

    // 真实创建 payload 断言：不是纯函数推断，而是实际 POST /games/create 的 body
    vm.step = 4
    vm.aiPrompt = '一个由 AI 生成世界的单人局'
    vm.characters = [{ character_name: '旅人', identity: {}, attributes: {}, skills: [] }]
    await vm.create()
    await flushPromises()
    const body = postedBody('/games/create')
    expect(body).not.toBeNull()
    expect(body?.play_mode).toBe('free')
    expect(body?.rule_id).toBe('freeform_srd')
    expect(body?.solo).toBe(true)
    expect(mocks.router.push).toHaveBeenCalledWith({ name: 'play', query: { game: 'g1' } })
    wrapper.unmount()
  })

  it('disables the multiplayer option while the GAL rule is active', async () => {
    const { wrapper, vm } = await mountWithGalaRuleSelected()
    vm.step = 2 // 游戏模式下拉在第二步才渲染
    await flushPromises()
    const gameModeLabel = wrapper.findAll('label').find(label => label.find('span').text() === '游戏模式')
    expect(gameModeLabel).toBeDefined()
    const gameModeSelect = gameModeLabel!.get('select').element as HTMLSelectElement
    const multiplayerOption = Array.from(gameModeSelect.options).find(option => option.value === 'false')
    expect(multiplayerOption?.disabled).toBe(true)
    wrapper.unmount()
  })

  it('keeps template GAL legal when leaving to custom source and coming back', async () => {
    const { wrapper, vm } = await mountWithGalaRuleSelected()
    const sourceButtons = () => wrapper.findAll('.create-world-source .create-mode-cards button')
    await sourceButtons()[1].trigger('click') // 自定义来源
    await flushPromises()
    // 自定义来源沿用模板的生效规则：GAL 组合仍然合法，不被破坏
    expect(vm.activeRule).toBe('gal_narrative')
    expect(vm.playMode).toBe('gal')
    expect(vm.solo).toBe(true)
    await sourceButtons()[0].trigger('click') // 回到模板
    await flushPromises()
    expect(vm.playMode).toBe('gal')
    expect(vm.solo).toBe(true)
    wrapper.unmount()
  })

  it('an AI-generated rule keeps the play mode legal before and after generation lands', async () => {
    const { wrapper, vm } = await mountWithGalaRuleSelected()
    await wrapper.findAll('.create-world-source .create-mode-cards button')[2].trigger('click')
    await flushPromises()
    expect(vm.playMode).toBe('free')
    // AI 生成规则到达：生效规则变化，play_mode 仍保持合法
    vm.aiGeneratedRule = { rule_id: 'generated:custom', rule_name: '自定义', ruleset_runtime: { id: 'custom', version: 1, capabilities: {} } }
    await flushPromises()
    expect(vm.activeRule).toBe('generated:custom')
    expect(vm.playMode).toBe('free')
    wrapper.unmount()
  })
})
