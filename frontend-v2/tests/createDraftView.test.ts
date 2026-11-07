import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CreateView from '../src/features/create/CreateView.vue'
import { i18n } from '../src/i18n'
import { clearAllCreateDraftsForTests, loadCreateDraft, setCreateDraftSession } from '../src/features/create/createDraft'

const mocks = vi.hoisted(() => ({
  route: { query: { return_to: 'library', adventure_id: 'core:story', module_id: 'core' } as Record<string, string> },
  router: { push: vi.fn(), resolve: vi.fn(() => ({ href: '/adventures' })) },
  apiPaths: [] as string[],
}))

vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => mocks.router }))
vi.mock('../src/stores/useSettingsStore', () => ({ useSettingsStore: () => ({ config: {}, error: '', load: vi.fn().mockResolvedValue(undefined) }) }))
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
vi.mock('../src/api/client', () => ({ api: vi.fn(async (path: string) => {
  mocks.apiPaths.push(path)
  if (path.startsWith('/world-templates')) return { templates: [{ world_id: 'greymoor', world_name: '灰沼', default_rule: 'dnd2024_srd' }] }
  if (path.startsWith('/rules?')) return { rules: [
    { rule_id: 'dnd2024_srd', rule_name: 'D&D', ruleset_runtime: { id: 'core:dnd2024', version: 1, capabilities: { character_builder: 'professional', adventure_formats: ['diceframe:adventure-graph-v2'] } } },
    { rule_id: 'gal_narrative', rule_name: 'GAL Narrative Preview' },
  ] }
  if (path === '/worlds') return { worlds: [] }
  if (path.startsWith('/lorebooks?')) return { books: [
    { id: 'world:greymoor', name: '灰沼基础资料', enabled: true, entry_count: 4, bindings: [{ scope_kind: 'world', scope_id: 'greymoor', role: 'primary' }] },
    { id: 'npc', name: '港口人物', enabled: true, entry_count: 0, bindings: [{ scope_kind: 'world', scope_id: 'greymoor' }] },
  ] }
  if (path === '/character-cards') return { cards: [] }
  if (path.startsWith('/adventures')) return { ok: true, adventures: [{ adventure_id: 'core:story', source_kind: 'plugin', source_id: 'core', format: 'diceframe:adventure-graph-v2', required_runtime: { id: 'core:dnd2024', minimum_version: 1 }, world_policy: 'portable', compatibility: 'compatible', name: 'Core story' }] }
  if (path.startsWith('/config')) return { ok: true }
  return { ok: true }
}), errorMessage: (error: unknown) => String(error), apiBlob: vi.fn() }))
vi.mock('../src/api/modules', () => ({ moduleApi: { list: vi.fn().mockResolvedValue({ modules: [] }) } }))

const stubs = {
  CharacterWizard: true, CharacterCardPicker: true, PortraitImage: true, AdventureSceneImagePicker: true,
  MapBackgroundPicker: true, RulesetExperienceHost: true, AdventureLaunchSummary: true,
}

describe('CreateView draft restoration', () => {
  afterEach(() => {
    clearAllCreateDraftsForTests()
    setCreateDraftSession(null)
    mocks.router.push.mockReset()
    mocks.route.query = { return_to: 'library', adventure_id: 'core:story', module_id: 'core' }
  })

  it('keeps GAL in the rule selector without a separate experimental checkbox', async () => {
    mocks.route.query = {}
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const ruleSettings = wrapper.get('details.create-rule-settings')
    expect(ruleSettings.find('input[type=checkbox]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('GAL 预览（实验性）')
    expect(wrapper.text()).not.toContain('尚无好感度、路线与结局')
    const ruleSelect = ruleSettings.get('select')
    expect(ruleSelect.find('option[value="gal_narrative"]').exists()).toBe(true)
    await ruleSelect.setValue('gal_narrative')
    await flushPromises()
    expect(ruleSettings.get('summary').text()).toContain('GAL Narrative Preview')
    expect((ruleSelect.element as HTMLSelectElement).value).toBe('gal_narrative')
    wrapper.unmount()
  })

  it('defaults to compact rule and book summaries while keeping their choices available', async () => {
    mocks.route.query = {}
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const ruleSettings = wrapper.get('details.create-rule-settings')
    const bookSettings = wrapper.get('details.create-lorebook-summary')
    expect(ruleSettings.attributes('open')).toBeUndefined()
    expect(ruleSettings.get('summary').text()).toContain('D&D')
    expect(bookSettings.attributes('open')).toBeUndefined()
    expect(bookSettings.get('summary').text()).toContain('已选 2 册')
    expect(wrapper.text()).not.toContain('当前规则没有兼容的冒险能力')
    expect(wrapper.find('.create-source-summary').exists()).toBe(false)
    expect(wrapper.get('.create-play-mode').element.compareDocumentPosition(wrapper.get('.create-world-source').element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await ruleSettings.get('summary').trigger('click')
    await bookSettings.get('summary').trigger('click')
    await bookSettings.get('input[type=checkbox]').setValue(false)
    await flushPromises()
    expect(bookSettings.get('summary').text()).toContain('已选 1 册')
    expect((wrapper.vm as any).selectedBookIds).toEqual(['npc'])
    await bookSettings.get('[data-testid=lore-source-mode]').setValue('__blank__')
    await flushPromises()
    expect((wrapper.vm as any).loreChoice).toBe('__blank__')
    expect(bookSettings.get('summary').text()).toContain('新世界')
    wrapper.unmount()
  })

  it('keeps STEP labels and the current mode, rule and party size in the side rail', async () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    expect(wrapper.findAll('.create-step-rail__item small').map(item => item.text())).toEqual(['STEP 01', 'STEP 02', 'STEP 03', 'STEP 04'])
    expect(wrapper.find('.create-rail-summary').text()).toContain('D&D')
    const vm = wrapper.vm as any
    vm.solo = false
    vm.characters = [{ character_name: '调查员' }, { character_name: '同伴' }]
    await flushPromises()
    expect(wrapper.find('.create-rail-summary').text()).toContain('多人')
    expect(wrapper.find('.create-rail-summary [data-testid="create-rail-party-count"]').text()).toBe('2')
    expect(wrapper.find('.create-page-header__eyebrow').text()).toBe('NEW GAME')
    wrapper.unmount()
  })

  it('restores step, selected adventure, character control and professional fields on remount without a password', async () => {
    const first = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const vm = first.vm as any
    const nameInput = first.find('.create-config-surface input')
    await nameInput.setValue('灰沼回归草稿')
    await first.find('.create-launch-advanced input').setValue('typed-seed')
    vm.step = 3
    vm.playMode = 'adventure'
    vm.adventureChoice = '["plugin","core","core:story"]'
    vm.characters = [{ character_name: '专业角色', portrait: { kind: 'seed', value: 'portrait' }, ruleset_character: { build: { level: 1 } }, identity: {}, attributes: {}, skills: [] }]
    vm.cardControl = ['ai']
    vm.roomPassword = 'never-save-this'
    vm.mapBackgroundChoice = 'upload'
    vm.sceneImageFile = new File(['scene'], 'scene.png', { type: 'image/png' })
    vm.mapBackgroundFile = new File(['map'], 'map.png', { type: 'image/png' })
    vm.gameLanguage = 'en'
    await flushPromises()
    mocks.route.query = {}
    first.unmount()

    mocks.route.query = { return_to: 'library', adventure_id: 'core:story', module_id: 'core' }
    mocks.apiPaths = []
    const second = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const restored = second.vm as any
    expect(restored.step).toBe(3)
    expect(restored.name).toBe('灰沼回归草稿')
    expect(restored.adventureChoice).toBe('["plugin","core","core:story"]')
    expect(restored.characters[0].character_name).toBe('专业角色')
    expect(restored.characters[0].ruleset_character.build.level).toBe(1)
    expect(restored.cardControl[0]).toBe('ai')
    expect(restored.roomPassword).toBe('')
    expect(restored.seed).toBe('typed-seed')
    expect(restored.mapBackgroundChoice).toBe('upload')
    expect(restored.sceneImageFile?.name).toBe('scene.png')
    expect(restored.mapBackgroundFile?.name).toBe('map.png')
    expect(mocks.apiPaths.some(path => path.includes('world-templates?language=en'))).toBe(true)
    expect(mocks.router.push).not.toHaveBeenCalled()
    second.unmount()
  })

  it('does not reuse a draft for a different canonical adventure source', async () => {
    const first = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    await first.find('.create-config-surface input').setValue('只属于 Core')
    await flushPromises()
    first.unmount()
    mocks.route.query = { return_to: 'library', adventure_id: 'other:story', module_id: 'other' }
    const other = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    expect((other.vm as any).name).toBe('')
    other.unmount()
  })

  it('restores a generated AI rule without preparing another rule', async () => {
    const first = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const vm = first.vm as any
    vm.mode = 'ai'
    vm.aiPrompt = '自定义规则世界'
    vm.aiRule = 'dnd2024_srd'
    vm.aiAutoRule = true
    await flushPromises()
    vm.aiGeneratedRule = { rule_id: 'generated:custom', rule_name: '自定义规则', ruleset_runtime: { id: 'custom', version: 1, capabilities: {} } }
    await flushPromises()
    first.unmount()
    expect(loadCreateDraft({ source: JSON.stringify(['adventure', 'core', 'core:story']), seed: '' })?.aiGeneratedRule?.rule_id).toBe('generated:custom')
    mocks.route.query = { return_to: 'library', adventure_id: 'core:story', module_id: 'core' }
    const second = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const restored = second.vm as any
    expect(restored.aiGeneratedRule.rule_id).toBe('generated:custom')
    expect(restored.activeRule).toBe('generated:custom')
    second.unmount()
  })

  it('drops a legacy professional card when its review builder is canceled', async () => {
    i18n.global.locale.value = 'en'
    const rulesetBuilder = {
      template: '<button data-testid="ruleset-cancel" @click="$emit(\'cancel\')">Cancel</button>',
      emits: ['cancel'],
    }
    const wrapper = mount(CreateView, {
      global: {
        plugins: [i18n],
        stubs: { ...stubs, RulesetExperienceHost: rulesetBuilder },
      },
    })
    await flushPromises()
    const vm = wrapper.vm as any
    vm.characters = [{
      character_name: 'Legacy card',
      identity: {},
      attributes: { str: 10 },
      skills: [],
    }]
    vm.openWizard(0)
    await flushPromises()
    expect(vm.showRulesetBuilder).toBe(true)

    await wrapper.get('[data-testid="ruleset-cancel"]').trigger('click')
    expect(vm.characters).toHaveLength(0)
    expect(vm.showRulesetBuilder).toBe(false)
    wrapper.unmount()
  })

  it('keeps a canonical professional card when editing is canceled', async () => {
    i18n.global.locale.value = 'en'
    const rulesetBuilder = {
      template: '<button data-testid="ruleset-cancel" @click="$emit(\'cancel\')">Cancel</button>',
      emits: ['cancel'],
    }
    const wrapper = mount(CreateView, {
      global: {
        plugins: [i18n],
        stubs: { ...stubs, RulesetExperienceHost: rulesetBuilder },
      },
    })
    await flushPromises()
    const vm = wrapper.vm as any
    const canonicalCard = {
      character_name: 'Canonical card',
      identity: {},
      attributes: { str: 10 },
      skills: [],
      ruleset_character: { build: { level: 1 }, rule_binding: { rule_id: 'dnd2024_srd' } },
    }
    vm.characters = [canonicalCard]
    vm.openWizard(0)
    await flushPromises()
    await wrapper.get('[data-testid="ruleset-cancel"]').trigger('click')

    expect(vm.characters).toHaveLength(1)
    expect(vm.characters[0].character_name).toBe('Canonical card')
    expect(vm.characters[0].ruleset_character).toEqual(canonicalCard.ruleset_character)
    wrapper.unmount()
  })

  it('returns a restored legacy card to professional review before confirming or posting creation', async () => {
    const wrapper = mount(CreateView, { global: { plugins: [i18n], stubs } })
    await flushPromises()
    const vm = wrapper.vm as any
    vm.characters = [{ character_name: 'Legacy draft', identity: {}, attributes: { str: 10 }, skills: [] }]
    vm.step = 3
    await vm.nextStep()
    expect(vm.step).toBe(3)
    expect(vm.showRulesetBuilder).toBe(true)
    expect(vm.error).toContain(i18n.global.t('apiErrors.PROFESSIONAL_CHARACTER_BLUEPRINT_REQUIRED'))
    vm.showRulesetBuilder = false
    vm.step = 4
    mocks.apiPaths.length = 0
    await vm.create()
    expect(vm.step).toBe(3)
    expect(vm.showRulesetBuilder).toBe(true)
    expect(mocks.apiPaths).not.toContain('/games/create')
    wrapper.unmount()
  })
})
