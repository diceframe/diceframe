import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createWebHashHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import Dnd5eImportView from '../src/features/dnd5e-import/Dnd5eImportView.vue'
import Dnd5eReportPanel from '../src/features/dnd5e-import/Dnd5eReportPanel.vue'
import SelectionsForm from '../src/features/dnd5e-import/SelectionsForm.vue'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import type { JsonObject, RulesetBuilderChoices, RulesetChoice } from '../src/api/types'
import type {
  Dnd5eActorDisplay,
  Dnd5eConversionReport,
  Dnd5eMissingEntry,
  Dnd5ePreviewResponse,
  Dnd5eSelections,
} from '../src/api/dnd5eImport'

const GAME = { game_key: 'one', world_name: 'Test World', rule_id: 'dnd2024_srd', gm_uid: 'gm-user' }
const ACTOR: JsonObject = { name: 'Dain Ironfoot', type: 'character', system: {} }

function choice(ref: string, name: string): RulesetChoice {
  return { ref, id: ref.split(':')[1] || ref, name, summary: '', automation_level: 'deterministic', source_ref: '' }
}

function choicesPool(overrides: Partial<RulesetBuilderChoices> = {}): RulesetBuilderChoices {
  return {
    ability_methods: [], classes: [choice('class:fighter', 'Fighter')],
    species: [choice('species:dwarf', 'Dwarf')], backgrounds: [choice('background:soldier', 'Soldier')],
    class_skills: [choice('skill:history', 'History'), choice('skill:perception', 'Perception')],
    class_skill_count: 2,
    equipment_packages: [choice('equipment_package:fighter_a', 'Fighter package A'), choice('equipment_package:fighter_b', 'Fighter package B')],
    background_equipment_packages: [choice('equipment_package:soldier_a', 'Soldier package A')],
    background_ability_refs: ['ability:str', 'ability:con', 'ability:wis'],
    species_sizes: [], species_choices: [], species_skills: [], species_skill_count: 0,
    species_feats: [], species_feat_count: 0, feat_choices: [], class_tools: [], class_tool_count: 0,
    recommended_base_abilities: {}, skills: [],
    languages: [choice('language:common', 'Common'), choice('language:dwarvish', 'Dwarvish'), choice('language:elvish', 'Elvish')],
    origin_feats: [], quick_presets: [], class_spells: {}, recommended_class_spells: {},
    ...overrides,
  }
}

function display(overrides: Partial<Dnd5eActorDisplay> = {}): Dnd5eActorDisplay {
  return {
    name: 'Dain Ironfoot', type: 'character', image: '', level: 1, alignment: 'lg',
    hp: { value: 13, max: 13, temp: 0, declared: true }, armor_class: 18, speed: 30, declared: true,
    species: { name: 'Dwarf', ref: 'species:dwarf', raw_name: 'Dwarf', raw_identifier: 'dwarf' },
    background: { name: 'Soldier', ref: 'background:soldier', raw_name: 'Soldier', raw_identifier: 'soldier' },
    class: [{ ref: 'class:fighter', levels: 1, name: 'Fighter', raw_name: 'Fighter', raw_identifier: 'fighter' }],
    abilities: { str: { score: 17, declared: true }, dex: { score: 12, declared: true } },
    skills: [{ id: 'athletics', ref: 'skill:athletics', proficiency: 1, declared: true }],
    languages: [{ key: 'common', ref: 'language:common' }],
    items: [
      {
        name: 'Longsword', type: 'weapon', identifier: 'longsword', image: '', quantity: 1,
        weight: { value: 3, units: 'lb' }, price: { value: 15, denomination: 'gp' },
        damage: { formula: '1d8', types: ['slashing'], declared: true }, armor: { value: null, dex: null },
        properties: ['ver'], description: '', declared: true, catalog_ref: 'item:longsword',
      },
      {
        name: 'Blade of Testing', type: 'weapon', identifier: 'blade-of-testing', image: '', quantity: 1,
        weight: null, price: null, damage: { formula: '2d6', types: ['fire'], declared: true },
        armor: { value: null, dex: null }, properties: ['mgc'], description: '', declared: true, catalog_ref: null,
      },
    ],
    ...overrides,
  }
}

function report(overrides: Partial<Dnd5eConversionReport> = {}): Dnd5eConversionReport {
  return {
    source_version: '6.1.2',
    level: 'saved_not_runnable',
    issues: [],
    unsupported: [{ path: 'items[6]', kind: 'unsupported', detail: 'weapon item blade-of-testing has no dnd2024 catalog item' }],
    preserved_fields: ['items[6]'],
    unknown_fields: ['system.someFutureField'],
    missing_references: [{ kind: 'item', path: 'items[6]', external_id: 'blade-of-testing' }],
    warnings: ['background_ability_bonuses is left empty for the user to confirm'],
    runtime_reconciliation: [],
    contains_script: false,
    ...overrides,
  }
}

const MISSING: Dnd5eMissingEntry[] = [
  { field: 'background_ability_bonuses', reason: 'choose the background split: +2/+1 or +1/+1/+1' },
  { field: 'equipment_package_ref', reason: 'choose the class starting equipment package' },
  { field: 'background_equipment_package_ref', reason: 'choose the background starting equipment package' },
  { field: 'language_refs', reason: 'choose exactly three different Standard languages, including Common' },
]

function draft(): JsonObject {
  return {
    name: 'Dain Ironfoot', level: 1, ability_method: 'rolled',
    base_abilities: { str: 17, dex: 12, con: 16, int: 9, wis: 13, cha: 8 },
    background_ability_bonuses: {},
    class_ref: 'class:fighter', species_ref: 'species:dwarf', background_ref: 'background:soldier',
    class_skill_refs: ['skill:history', 'skill:perception'], class_tool_refs: [],
    equipment_package_ref: null, background_equipment_package_ref: null,
    language_refs: ['language:common', 'language:dwarvish'],
    species_size: null, species_choice_answers: {}, species_skill_refs: [], species_feat_refs: [],
    feat_choice_answers: {}, locale: 'en',
  }
}

function previewResponse(overrides: Partial<Dnd5ePreviewResponse> = {}): Dnd5ePreviewResponse {
  return {
    ok: true, valid: false, draft: draft(), missing: MISSING, errors: [],
    display: display(), report: report(), reconciliation: [],
    source_unresolved: [{ field: 'equipment_package_ref', reason: 'dnd5e does not record the chosen starting package' }],
    ...overrides,
  }
}

function validPreview(): Dnd5ePreviewResponse {
  const reconciliation = [{ field: 'hp.max', declared: 13, recomputed: 12, note: 'dnd2024 recomputed maximum HP' }]
  return previewResponse({
    valid: true, missing: [],
    character: { character_name: 'Dain Ironfoot', ruleset_character: {} },
    reconciliation,
    report: report({ level: 'convertible', runtime_reconciliation: reconciliation }),
  })
}

function commitResponse() {
  return {
    ok: true,
    created: { user_id: 'player-1', character_name: 'Dain Ironfoot', reused: false },
    character: { character_name: 'Dain Ironfoot' },
    report: report({ level: 'convertible' }),
    reconciliation: [{ field: 'hp.max', declared: 13, recomputed: 12, note: 'dnd2024 recomputed maximum HP' }],
  }
}

interface ApiBehavior {
  preview?: (callCount: number) => Promise<unknown>
  commit?: () => Promise<unknown>
  choices?: RulesetBuilderChoices
}

function makeApi(behavior: ApiBehavior = {}) {
  let previewCalls = 0
  return vi.fn((path: string) => {
    if (path === '/games') return Promise.resolve({ games: [GAME] })
    if (path.endsWith('/dnd5e/characters/preview')) {
      previewCalls += 1
      return behavior.preview
        ? behavior.preview(previewCalls)
        : Promise.resolve(previewResponse())
    }
    if (path.includes('/builder/choices')) {
      return Promise.resolve({ ok: true, rule_id: 'dnd2024_srd', choices: behavior.choices || choicesPool() })
    }
    if (path.endsWith('/dnd5e/characters/commit')) {
      return behavior.commit ? behavior.commit() : Promise.resolve(commitResponse())
    }
    return Promise.reject(new Error(`unexpected ${path}`))
  })
}

const Dummy = { template: '<div />' }

async function open(apiMock: ReturnType<typeof makeApi>) {
  vi.spyOn(client, 'api').mockImplementation(apiMock as unknown as typeof client.api)
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/', component: Dummy },
      { path: '/dnd5e-import', name: 'dnd5e-import', component: Dummy },
      { path: '/play', name: 'play', component: Dummy },
    ],
  })
  router.push('/dnd5e-import?game=one')
  await router.isReady()
  const wrapper = mount(Dnd5eImportView, { global: { plugins: [i18n, router] } })
  await flushPromises()
  return wrapper
}

async function chooseFile(wrapper: Awaited<ReturnType<typeof open>>, content: string) {
  const input = wrapper.get('[data-testid="dnd5e-import-file"]')
  const file = new File([content], 'actor.json', { type: 'application/json' })
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

async function runPreview(wrapper: Awaited<ReturnType<typeof open>>) {
  await wrapper.get('[data-testid="dnd5e-import-preview"]').trigger('click')
  await flushPromises()
}

function bodyOf(apiMock: ReturnType<typeof makeApi>, suffix: string): Record<string, unknown> {
  const calls = apiMock.mock.calls as unknown as [string, RequestInit][]
  const call = calls.find(entry => entry[0].endsWith(suffix))
  expect(call, `expected a ${suffix} request`).toBeTruthy()
  return JSON.parse(call![1].body as string) as Record<string, unknown>
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  localStorage.setItem('trpg_access_token', 'test-token')
})

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
  localStorage.clear()
})

describe('Dnd5eImportView', () => {
  it('previews the actor and renders the display DTO, report and missing choices', async () => {
    const apiMock = makeApi()
    const wrapper = await open(apiMock)
    await chooseFile(wrapper, JSON.stringify(ACTOR))
    await runPreview(wrapper)

    const body = bodyOf(apiMock, '/preview')
    expect(body.actor).toEqual(ACTOR)
    expect(body.selections).toEqual({})
    expect(apiMock.mock.calls.some(entry => String(entry[0]).includes('/builder/choices'))).toBe(true)

    expect(wrapper.get('[data-testid="dnd5e-import-display"]').text()).toContain('Dain Ironfoot')
    expect(wrapper.get('[data-testid="dnd5e-item-mapped"]').text()).toContain('item:longsword')
    expect(wrapper.get('[data-testid="dnd5e-item-unmapped"]').text()).toContain('Saved, not runnable')
    expect(wrapper.get('[data-testid="dnd5e-unsupported"]').text()).toContain('blade-of-testing')
    expect(wrapper.get('[data-testid="dnd5e-level-badge"]').text()).toContain('Saved, not runnable')
    expect(wrapper.get('[data-testid="dnd5e-missing-list"]').text()).toContain('background_ability_bonuses')
    expect(wrapper.get('[data-testid="dnd5e-source-unresolved"]').text()).toContain('equipment_package_ref')
    expect(wrapper.find('[data-testid="dnd5e-selection-form"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="dnd5e-import-commit"]').attributes('disabled')).toBeDefined()
  })

  it('collects selections, marks the preview stale, then commits after re-preview', async () => {
    const apiMock = makeApi({ preview: count => Promise.resolve(count >= 2 ? validPreview() : previewResponse()) })
    const wrapper = await open(apiMock)
    await chooseFile(wrapper, JSON.stringify(ACTOR))
    await runPreview(wrapper)

    await wrapper.get('[data-testid="dnd5e-bonus-2-1"]').trigger('click')
    expect(wrapper.get('[data-testid="dnd5e-import-stale"]').text()).toContain('Choices changed')
    expect(wrapper.get('[data-testid="dnd5e-import-commit"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-testid="dnd5e-select-equipment_package_ref"]').setValue('equipment_package:fighter_b')
    await wrapper.get('[data-testid="dnd5e-select-background_equipment_package_ref"]').setValue('equipment_package:soldier_a')
    await wrapper.get('[data-testid="dnd5e-check-language_refs-language:elvish"]').setValue(true)

    await wrapper.get('[data-testid="dnd5e-import-repreview"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="dnd5e-import-stale"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="dnd5e-import-commit"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="dnd5e-reconciliation"]').text()).toContain('hp.max')

    await wrapper.get('[data-testid="dnd5e-import-commit"]').trigger('click')
    await flushPromises()

    const selections = bodyOf(apiMock, '/commit').selections as Dnd5eSelections
    expect(selections.background_ability_bonuses).toEqual({ str: 2, con: 1 })
    expect(selections.equipment_package_ref).toBe('equipment_package:fighter_b')
    expect(selections.background_equipment_package_ref).toBe('equipment_package:soldier_a')
    expect(selections.language_refs).toEqual(['language:common', 'language:dwarvish', 'language:elvish'])
    expect(wrapper.get('[data-testid="dnd5e-import-receipt"]').text()).toContain('Dain Ironfoot')
    expect(wrapper.get('[data-testid="dnd5e-receipt-name"]').text()).toContain('Dain Ironfoot')
  })

  it('blocks creation above level 1 without progression (fail closed)', async () => {
    const apiMock = makeApi({
      preview: () => Promise.resolve(previewResponse({
        draft: { ...draft(), level: 3 },
        missing: [{ field: 'progression.history', reason: 'level 3 requires progression.history' }, ...MISSING],
      })),
    })
    const wrapper = await open(apiMock)
    await chooseFile(wrapper, JSON.stringify(ACTOR))
    await runPreview(wrapper)

    expect(wrapper.get('[data-testid="dnd5e-level-blocked"]').text()).toContain('Above level 1')
    expect(wrapper.get('[data-testid="dnd5e-import-commit"]').attributes('disabled')).toBeDefined()
  })

  it('prompts a re-preview when commit reports an incomplete draft and recovers', async () => {
    let incomplete = true
    const apiMock = makeApi({
      preview: () => Promise.resolve(validPreview()),
      commit: () => incomplete
        ? Promise.reject(new ApiError('draft not complete', 422, 'dnd5e_creation_incomplete'))
        : Promise.resolve(commitResponse()),
    })
    const wrapper = await open(apiMock)
    await chooseFile(wrapper, JSON.stringify(ACTOR))
    await runPreview(wrapper)
    await wrapper.get('[data-testid="dnd5e-import-commit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="dnd5e-import-stale"]').text()).toContain('fresh confirmation')
    expect(wrapper.get('[data-testid="dnd5e-import-error"]').text()).toContain('incomplete')
    expect(wrapper.find('[data-testid="dnd5e-import-receipt"]').exists()).toBe(false)

    incomplete = false
    await wrapper.get('[data-testid="dnd5e-import-repreview"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="dnd5e-import-commit"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="dnd5e-import-receipt"]').exists()).toBe(true)
  })

  it('localizes backend error codes in both core locales', async () => {
    const apiMock = makeApi({ preview: () => Promise.reject(new ApiError('raw backend text', 422, 'dnd5e_ruleset_required')) })
    const wrapper = await open(apiMock)
    await chooseFile(wrapper, JSON.stringify(ACTOR))
    await runPreview(wrapper)
    expect(wrapper.get('[data-testid="dnd5e-import-error"]').text()).toContain('D&D 2024 game')

    i18n.global.locale.value = 'zh-CN'
    await runPreview(wrapper)
    expect(wrapper.get('[data-testid="dnd5e-import-error"]').text()).toContain('只能导入到 D&D 2024 规则的对局')
  })

  it('rejects a file that is not a JSON object without calling preview', async () => {
    const apiMock = makeApi()
    const wrapper = await open(apiMock)
    await chooseFile(wrapper, 'this is not json')
    expect(wrapper.get('[data-testid="dnd5e-import-error"]').text()).toContain('not a valid JSON object')
    expect(apiMock.mock.calls.some(entry => String(entry[0]).endsWith('/preview'))).toBe(false)
  })

  it('shows the GM-only notice and disables import without an owner session', async () => {
    localStorage.removeItem('trpg_access_token')
    const apiMock = makeApi()
    const wrapper = await open(apiMock)
    expect(wrapper.find('[data-testid="dnd5e-import-gm-only"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="dnd5e-import-file"]').attributes('disabled')).toBeDefined()
  })
})

describe('SelectionsForm', () => {
  const baseProps = {
    draft: draft(), choices: choicesPool(), missing: MISSING, selections: {} as Dnd5eSelections,
  }

  it('writes feat follow-up answers as feat_choice_answers', async () => {
    const choices = choicesPool({
      feat_choices: [{
        feat_ref: 'feat:savage_attacker', name: 'Savage Attacker', summary: '',
        automation_level: 'guided', source_ref: '',
        specs: [{ id: 'weapon', name: 'Weapon', count: 1, options: [{ value: 'martial', name: 'Martial', source_ref: '' }] }],
      }],
    })
    const wrapper = mount(SelectionsForm, {
      props: { ...baseProps, choices, missing: [{ field: 'feat_choice_answers.feat:savage_attacker.weapon', reason: 'choose exactly 1 option(s)' }] },
      global: { plugins: [i18n] },
    })
    await wrapper.get('[data-testid="dnd5e-feat-feat:savage_attacker-weapon-martial"]').setValue(true)
    const emitted = wrapper.emitted('change') as [Dnd5eSelections][]
    expect(emitted).toHaveLength(1)
    expect(emitted[0][0].feat_choice_answers).toEqual({ 'feat:savage_attacker': { weapon: ['martial'] } })
  })

  it('writes feature branch picks as feature_choices records', async () => {
    const choices = choicesPool({
      feature_option_choices: [{
        owner: 'class:cleric', feature_id: 'divine_order', choice_id: 'order', count: 1,
        options: [{ value: 'feature:protector', name: 'Protector' }],
      }],
    } as unknown as Partial<RulesetBuilderChoices>)
    const wrapper = mount(SelectionsForm, {
      props: { ...baseProps, choices, missing: [{ field: 'feature_choices.class:cleric.divine_order.order', reason: 'choose exactly 1 option(s)' }] },
      global: { plugins: [i18n] },
    })
    await wrapper.get('[data-testid="dnd5e-feature-class:cleric-divine_order-order-feature:protector"]').setValue(true)
    const emitted = wrapper.emitted('change') as [Dnd5eSelections][]
    expect(emitted[0][0].feature_choices).toEqual([{
      owner: 'class:cleric', feature_id: 'divine_order', choice_id: 'order', selected_refs: ['feature:protector'],
    }])
  })

  it('collects class spell cantrips into class_spell_choices', async () => {
    const choices = choicesPool({
      class_spells: {
        requirements: {
          class_ref: 'class:wizard', level: 1, cantrip_count: 1, prepared_spell_count: 0,
          spellbook_minimum: 0, maximum_spell_level: 1, slot_profile: 'full', spell_slots: {},
        },
        cantrips: [{
          ref: 'spell:light', id: 'light', name: 'Light', level: 0, school: 'evocation',
          class_refs: ['class:wizard'], casting_time: 'action', range: 'touch', components: ['V'],
          ritual: false, concentration: false, duration: '1 hour', source_ref: '',
        }],
        leveled_spells: [],
      },
    })
    const wrapper = mount(SelectionsForm, { props: { ...baseProps, choices }, global: { plugins: [i18n] } })
    await wrapper.get('[data-testid="dnd5e-spell-cantrip-spell:light"]').setValue(true)
    const emitted = wrapper.emitted('change') as [Dnd5eSelections][]
    expect(emitted[0][0].class_spell_choices?.cantrip_refs).toEqual(['spell:light'])
  })
})

describe('Dnd5eReportPanel', () => {
  it('shows an empty state when there are no issues', () => {
    const wrapper = mount(Dnd5eReportPanel, {
      props: { report: report({ level: 'convertible', unsupported: [], preserved_fields: [], unknown_fields: [], missing_references: [], warnings: [] }) },
      global: { plugins: [i18n] },
    })
    expect(wrapper.text()).toContain('No conversion issues.')
    expect(wrapper.find('[data-testid="dnd5e-unsupported"]').exists()).toBe(false)
  })

  it('renders the reconciliation table with declared and recomputed values', () => {
    const wrapper = mount(Dnd5eReportPanel, {
      props: {
        report: report(),
        reconciliation: [{ field: 'armor_class', declared: 18, recomputed: 16, note: 'recomputed from equipment' }],
      },
      global: { plugins: [i18n] },
    })
    const row = wrapper.get('[data-testid="dnd5e-reconciliation-row"]')
    expect(row.text()).toContain('armor_class')
    expect(row.text()).toContain('18')
    expect(row.text()).toContain('16')
  })
})