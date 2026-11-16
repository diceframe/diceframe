import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CreateView from '../src/features/create/CreateView.vue'
import { i18n } from '../src/i18n'
import { clearAllCreateDraftsForTests, setCreateDraftSession } from '../src/features/create/createDraft'

const mocks = vi.hoisted(() => ({
  route: { query: {} as Record<string, string> },
  router: { push: vi.fn(), resolve: vi.fn(() => ({ href: '/adventures' })) },
}))

/** 一个 FU 形态的冒险：自带世界由模组生成，包里没有 recommended_world_id 对应的世界。 */
const MODULE_ADVENTURE = {
  adventure_id: 'press_start',
  version: '1.0.0',
  format: 'diceframe:adventure-graph-v2',
  world_policy: 'portable',
  recommended_world_id: 'press_start',
  required_runtime: { id: 'fu:core', minimum_version: 1 },
  name: '冒险开始（Press Start）',
  summary: '飞空艇坠落梅基多巨坑',
  estimated_minutes: 60,
  compatibility: 'compatible',
  incompatibility_reasons: [],
  directory_id: 'press_start',
  source: 'custom',
  source_kind: 'plugin',
  source_id: 'fu-adventures',
  custom: true,
}

vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => mocks.router }))
vi.mock('../src/stores/useSettingsStore', () => ({
  useSettingsStore: () => ({ config: {}, error: '', load: vi.fn().mockResolvedValue(undefined) }),
}))
vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
vi.mock('../src/api/sceneImages', () => ({
  resolveSceneImageUrl: vi.fn().mockResolvedValue(''),
  revokeSceneImageUrl: vi.fn(),
  uploadSceneImage: vi.fn(),
  sceneImageStyle: vi.fn(() => ({})),
  sceneImageCandidates: vi.fn((primary: unknown, variants: unknown[] = []) => primary ? [primary, ...variants] : variants),
  pickSceneImageReference: vi.fn((primary: unknown, variants: unknown[]) => primary || variants[0]),
}))
vi.mock('../src/api/mapBackgrounds', () => ({ mapBackgroundSelection: vi.fn(() => null), uploadMapBackground: vi.fn() }))
vi.mock('../src/api/client', () => ({
  api: vi.fn(async (path: string) => {
    if (path.startsWith('/world-templates')) {
      return { templates: [{ world_id: 'greymoor', world_name: '灰沼边境', default_rule: 'dnd2024_srd' }] }
    }
    if (path.startsWith('/rules?')) {
      return {
        rules: [
          { rule_id: 'dnd2024_srd', rule_name: 'D&D 2024', ruleset_runtime: { id: 'core:dnd2024', version: 1, capabilities: { character_builder: 'professional', adventure_formats: ['diceframe:adventure-graph-v2'] } } },
          { rule_id: 'fabula_ultima', rule_name: '最终物语 Fabula Ultima', ruleset_runtime: { id: 'fu:core', version: 1, capabilities: { character_builder: 'legacy', adventure_formats: ['diceframe:adventure-graph-v2'] } } },
        ],
      }
    }
    if (path === '/worlds') return { worlds: [{ world_id: 'greymoor', name: '灰沼边境' }] }
    if (path.startsWith('/lorebooks?')) return { books: [] }
    if (path === '/character-cards') return { cards: [] }
    if (path.startsWith('/adventures')) return { ok: true, adventures: [MODULE_ADVENTURE] }
    if (path.startsWith('/modules/fu-adventures/lorebooks')) return { ok: true, lorebooks: [] }
    if (path.startsWith('/config')) return { ok: true }
    return { ok: true }
  }),
  errorMessage: (error: unknown) => String(error),
  apiBlob: vi.fn(),
}))
vi.mock('../src/api/modules', () => ({
  moduleApi: { list: vi.fn().mockResolvedValue({ modules: [{ id: 'fu-adventures', name: '最终物语：起始冒险' }] }) },
}))

const stubs = {
  CharacterWizard: true, CharacterCardPicker: true, PortraitImage: true, AdventureSceneImagePicker: true,
  MapBackgroundPicker: true, RulesetExperienceHost: true, AdventureLaunchSummary: true,
}

async function pickModuleAdventure(wrapper: ReturnType<typeof mount>) {
  await wrapper.get('.create-play-mode button:nth-of-type(2)').trigger('click')
  await flushPromises()
  const selects = wrapper.findAll('.create-adventure-select-row select')
  await selects[0]!.setValue('fu-adventures')
  await flushPromises()
  const adventureSelect = wrapper.findAll('.create-adventure-select-row select')[1]!
  await adventureSelect.setValue(JSON.stringify(['plugin', 'fu-adventures', 'press_start']))
  await flushPromises()
  await flushPromises()
}

describe('CreateView 确认页的模组世界', () => {
  afterEach(() => {
    clearAllCreateDraftsForTests()
    setCreateDraftSession(null)
    mocks.router.push.mockReset()
  })

  it('冒险自带世界时，封面标题不再沿用上一个世界模板的名字', async () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.world).toBe('greymoor')
    expect(vm.activeRule).toBe('dnd2024_srd')

    await pickModuleAdventure(wrapper)

    // 选完模组：规则切到 FU，世界仍是旧模板（提交时由 effectiveWorldId 覆盖）。
    expect(vm.activeRule).toBe('fabula_ultima')
    expect(vm.playMode).toBe('adventure')

    vm.step = 4
    await flushPromises()

    const stage = wrapper.get('.create-confirm-stage')
    // 封面标题：过去回落到 selectedTemplateWorldName，显示成"灰沼边境"。
    expect(wrapper.get('.create-confirm-cover').text()).toContain('冒险开始（Press Start）')
    const cells = stage.get('.create-confirm-grid').findAll('article').map(item => item.text())
    expect(cells[0]).toContain('冒险开始（Press Start）')
    expect(cells[0]).not.toContain('灰沼边境')
    expect(cells[1]).toContain('最终物语 Fabula Ultima')
    // 模组世界没有额外资料册，不能再说"沿用当前世界的资料册"。
    expect(cells[0]).toContain('世界由模组自带')
    wrapper.unmount()
  })

  it('自由冒险仍按所选世界模板呈现封面与资料册', async () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const vm = wrapper.vm as any
    vm.step = 4
    await flushPromises()
    expect(wrapper.get('.create-confirm-cover').text()).toContain('灰沼边境')
    const cells = wrapper.get('.create-confirm-grid').findAll('article').map(item => item.text())
    expect(cells[0]).toContain('灰沼边境')
    expect(cells[0]).toContain('沿用当前世界的资料册')
    wrapper.unmount()
  })
})
