<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type {
  CharacterSheet,
  JsonObject,
  RulesetBuilderChoices,
  RulesetClassSpellChoices,
  RulesetChoice,
  RulesetExperience,
  RulesetQuickCharacterPreset,
  RulesetSelectedClassSpells,
  RulesetSpellChoice,
} from '@/api/types'
import {
  deriveRulesetBuilderCharacter,
  fetchRulesetBuilderChoices,
  finalizeRulesetBuilderCharacter,
  validateRulesetBuilderDraft,
} from '../api'
import type { Dnd2024Draft } from '../types'
import { useDnd2024BuilderStore } from '../store'
import ProgressRail from '../../shared/ProgressRail.vue'
import ChoiceGrid from '../../shared/ChoiceGrid.vue'

const props = withDefaults(defineProps<{
  ruleId: string
  language?: string
  initial?: CharacterSheet
  embedded?: boolean
  experience: RulesetExperience
}>(), { language: 'zh-CN', embedded: false })
const emit = defineEmits<{
  submit: [character: CharacterSheet, options?: { targetLevel?: number }]
  cancel: []
}>()

// Starting level is a flow, not a card property: `draft.level` stays 1 (that is
// what the builder validates and finalizes), and the host opens the advancement
// panel once per level after the card exists.  Editing an existing card has no
// starting level to choose, so the control is hidden there.
const targetLevel = ref(1)
const startLevelOptions = Array.from({ length: 20 }, (_, index) => index + 1)

type BuilderMode = 'quick' | 'guided' | 'expert'
type RefListKey = 'class_skill_refs' | 'species_skill_refs' | 'species_feat_refs' | 'class_tool_refs' | 'language_refs'
const ABILITIES = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const
const emptyChoices = (): RulesetBuilderChoices => ({
  ability_methods: [], classes: [], species: [], backgrounds: [],
  class_skills: [], class_skill_count: 0, equipment_packages: [],
  background_equipment_packages: [], background_ability_refs: [],
  species_sizes: [], species_choices: [], species_skills: [], species_skill_count: 0,
  species_feats: [], species_feat_count: 0, class_tools: [], class_tool_count: 0,
  feat_choices: [],
  recommended_base_abilities: {}, skills: [], languages: [], origin_feats: [],
  quick_presets: [], class_spells: {}, recommended_class_spells: {},
})

const store = useDnd2024BuilderStore()
const draft = computed(() => store.draft as Dnd2024Draft)
const choices = ref<RulesetBuilderChoices>(emptyChoices())
const mode = ref<BuilderMode>('quick')
const step = ref(1)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const preview = ref<JsonObject | null>(null)
const selectedPreset = ref('')
const spellSearch = ref('')
let choiceSequence = 0

const zh = computed(() => String(props.language || '').toLowerCase().startsWith('zh'))
const text = (cn: string, en: string) => zh.value ? cn : en

function selectMode(value: BuilderMode): void {
  mode.value = value
}

function onModeKey(event: KeyboardEvent): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  const buttons = Array.from(
    (event.currentTarget as HTMLElement).closest('[role="tablist"]')?.querySelectorAll<HTMLButtonElement>('[role="tab"]') || [],
  )
  if (!buttons.length) return
  const current = Math.max(0, buttons.indexOf(event.currentTarget as HTMLButtonElement))
  const nextIndex = event.key === 'Home' ? 0
    : event.key === 'End' ? buttons.length - 1
      : (current + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
  event.preventDefault()
  buttons[nextIndex]?.click()
  buttons[nextIndex]?.focus()
}
const steps = computed(() => [
  text('身份与方向', 'Identity & direction'),
  text('属性', 'Abilities'),
  text('熟练与装备', 'Proficiencies & gear'),
  text('审核', 'Review'),
])
const sourceVisible = computed(() => mode.value === 'expert')
const previewCanonical = computed<Record<string, unknown>>(() => preview.value || {})
const previewDerived = computed<Record<string, unknown>>(() => (
  previewCanonical.value.derived as Record<string, unknown> | undefined
) || {})
const previewResources = computed<Record<string, unknown>>(() => (
  previewCanonical.value.resources as Record<string, unknown> | undefined
) || {})

interface CompletionCheck {
  key: string
  label: string
  done: boolean
}

const ruleChoiceChecklist = computed<CompletionCheck[]>(() => {
  const checks: CompletionCheck[] = []
  const addCount = (key: string, cn: string, en: string, current: number, required: number) => {
    if (!required) return
    checks.push({
      key,
      label: text(`${cn}：${current}/${required}`, `${en}: ${current}/${required}`),
      done: current === required,
    })
  }
  addCount('class-skills', '职业技能', 'Class skills', draft.value.class_skill_refs.length, choices.value.class_skill_count)
  addCount('species-skills', '物种技能', 'Species skills', draft.value.species_skill_refs.length, choices.value.species_skill_count)
  addCount('species-feats', '物种专长', 'Species feats', draft.value.species_feat_refs.length, choices.value.species_feat_count)
  addCount('class-tools', '职业工具', 'Class tools', draft.value.class_tool_refs.length, choices.value.class_tool_count)
  if (choices.value.species_sizes.length > 1) {
    checks.push({
      key: 'species-size',
      label: text('体型：选择 1 项', 'Size: choose 1'),
      done: Boolean(draft.value.species_size),
    })
  }
  for (const choice of choices.value.species_choices) {
    checks.push({
      key: `species-choice-${choice.id}`,
      label: text(`${optionLabel(choice.id)}：选择 1 项`, `${optionLabel(choice.id)}: choose 1`),
      done: Boolean(draft.value.species_choice_answers[choice.id]),
    })
  }
  for (const feat of choices.value.feat_choices) {
    for (const spec of feat.specs) {
      const selected = draft.value.feat_choice_answers[feat.feat_ref]?.[spec.id] || []
      checks.push({
        key: `feat-${feat.feat_ref}-${spec.id}`,
        label: `${feat.name} · ${spec.name}：${selected.length}/${spec.count}`,
        done: selected.length === spec.count,
      })
    }
  }
  if (classSpells.value) {
    const requirements = classSpells.value.requirements
    addCount('cantrips', '戏法', 'Cantrips', selectedSpellRefs('cantrip').length, requirements.cantrip_count)
    addCount('spellbook', '法术书', 'Spellbook', selectedSpellRefs('spellbook').length, requirements.spellbook_minimum)
    addCount('prepared-spells', '准备法术', 'Prepared spells', selectedSpellRefs('prepared_spell').length, requirements.prepared_spell_count)
  }
  if (choices.value.equipment_packages.length) {
    checks.push({
      key: 'class-equipment', label: text('职业起始装备', 'Class starting equipment'),
      done: Boolean(draft.value.equipment_package_ref),
    })
  }
  if (choices.value.background_equipment_packages.length) {
    checks.push({
      key: 'background-equipment', label: text('背景起始装备', 'Background starting equipment'),
      done: Boolean(draft.value.background_equipment_package_ref),
    })
  }
  if (choices.value.languages.length) {
    checks.push({
      key: 'languages',
      label: text(`语言（含通用语）：${draft.value.language_refs.length}/3`, `Languages (including Common): ${draft.value.language_refs.length}/3`),
      done: draft.value.language_refs.length === 3 && draft.value.language_refs.includes('language:common'),
    })
  }
  return checks
})
const incompleteRuleChoices = computed(() => ruleChoiceChecklist.value.filter(item => !item.done))
const ruleChoicesReady = computed(() => !incompleteRuleChoices.value.length)
const pointBuyCosts: Record<number, number> = { 8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9 }
const pointBuySpent = computed(() => ABILITIES.reduce(
  (total, key) => total + (pointBuyCosts[Number(draft.value.base_abilities[key])] ?? 99), 0,
))
const pointBuyRemaining = computed(() => 27 - pointBuySpent.value)
// 职业推荐主属性：choices 暂未暴露 canonical 的 primary_ability_refs /
// spellcasting_ability，先用 recommended_base_abilities 的最高项推断；
// 未来协议暴露 canonical 字段后应改用 canonical，不再推断。
const recommendedPrimaryAbility = computed(() => {
  const entries = Object.entries(choices.value.recommended_base_abilities || {})
  if (!entries.length) return ''
  entries.sort((a, b) => Number(b[1]) - Number(a[1]))
  return entries[0]?.[0] || ''
})
const backgroundAllowedIds = computed(() =>
  choices.value.background_ability_refs.map(ref => ref.split(':')[1]).filter(Boolean))
const primaryAbilityBlockedByBackground = computed(() => {
  const primary = recommendedPrimaryAbility.value
  if (!primary) return false
  return backgroundAllowedIds.value.length > 0 && !backgroundAllowedIds.value.includes(primary)
})
const classSpells = computed<RulesetClassSpellChoices | null>(() => (
  choices.value.class_spells && 'requirements' in choices.value.class_spells
    ? choices.value.class_spells as RulesetClassSpellChoices
    : null
))
const visibleLeveledSpells = computed(() => {
  const query = spellSearch.value.trim().toLowerCase()
  const rows = classSpells.value?.leveled_spells || []
  if (!query) return rows
  return rows.filter(spell => [spell.name, spell.id, spell.school]
    .some(value => String(value).toLowerCase().includes(query)))
})

function blankDraft(): Dnd2024Draft {
  return {
    locale: props.language || props.experience.locale,
    name: '', level: 1, alignment: 'neutral_good', ability_method: 'standard_array',
    base_abilities: {}, background_ability_bonuses: {},
    class_ref: '', species_ref: '', background_ref: '',
    species_choice_answers: {}, species_skill_refs: [], species_feat_refs: [],
    feat_choice_answers: {},
    class_skill_refs: [], class_tool_refs: [], equipment_package_ref: '',
    background_equipment_package_ref: '', language_refs: ['language:common'],
    class_spell_choices: {},
  }
}

function draftFromInitial(initial?: CharacterSheet): Dnd2024Draft | undefined {
  const canonical = initial?.ruleset_character
  if (!canonical || typeof canonical !== 'object' || Array.isArray(canonical)) {
    if (!initial) return undefined
    return { ...blankDraft(), name: String(initial.character_name || '') }
  }
  const c = canonical as Record<string, unknown>
  const identity = (c.identity as Record<string, unknown> | undefined) || {}
  const build = (c.build as Record<string, unknown> | undefined) || {}
  const levels = Array.isArray(build.class_levels) ? build.class_levels : []
  const classLevel = (levels[0] as Record<string, unknown> | undefined) || {}
  return {
    ...blankDraft(),
    locale: String(c.locale || props.language || ''),
    name: String(identity.name || initial?.character_name || ''),
    level: Number(classLevel.level || build.level || 1),
    alignment: String(identity.alignment || 'neutral'),
    ability_method: String(build.ability_method || 'standard_array'),
    base_abilities: { ...((build.base_abilities as Record<string, number>) || {}) },
    background_ability_bonuses: { ...((build.background_ability_bonuses as Record<string, number>) || {}) },
    class_ref: String(classLevel.class_ref || ''),
    species_ref: String(identity.species_ref || ''),
    background_ref: String(identity.background_ref || ''),
    species_size: String(identity.size || ''),
    species_choice_answers: { ...((build.species_choice_answers as Record<string, string>) || {}) },
    species_skill_refs: [...((build.species_skill_refs as string[]) || [])],
    species_feat_refs: [...((build.species_feat_refs as string[]) || [])],
    feat_choice_answers: {
      ...((build.feat_choice_answers as Record<string, Record<string, string[]>>) || {}),
    },
    class_skill_refs: [...((build.class_skill_refs as string[]) || [])],
    class_tool_refs: [...((build.class_tool_refs as string[]) || [])],
    equipment_package_ref: String(build.equipment_package_ref || ''),
    background_equipment_package_ref: String(build.background_equipment_package_ref || ''),
    language_refs: [...((build.language_refs as string[]) || [])],
    class_spell_choices: {
      ...((build.class_spell_choices as RulesetSelectedClassSpells) || {}),
    },
  }
}

async function refreshChoices(): Promise<void> {
  const current = ++choiceSequence
  loading.value = true
  try {
    const result = await fetchRulesetBuilderChoices(props.ruleId, draft.value, props.language)
    if (current === choiceSequence) choices.value = result.choices
  } catch (cause: unknown) {
    if (current === choiceSequence) error.value = String((cause as Error)?.message || cause)
  } finally {
    if (current === choiceSequence) loading.value = false
  }
}

function choiceName(ref: string): string {
  const groups: RulesetChoice[][] = [
    choices.value.classes, choices.value.species, choices.value.backgrounds,
    choices.value.skills, choices.value.languages, choices.value.origin_feats,
    choices.value.equipment_packages, choices.value.background_equipment_packages,
    choices.value.class_tools,
  ]
  return groups.flat().find(item => item.ref === ref)?.name || ref.split(':').pop() || ref
}

function abilityName(id: string): string {
  const names: Record<string, [string, string]> = {
    str: ['力量', 'Strength'], dex: ['敏捷', 'Dexterity'], con: ['体质', 'Constitution'],
    int: ['智力', 'Intelligence'], wis: ['感知', 'Wisdom'], cha: ['魅力', 'Charisma'],
  }
  const pair = names[id] || [id, id]
  return zh.value ? pair[0] : pair[1]
}

function difficultyLabel(id: string): string {
  const labels: Record<string, [string, string]> = {
    beginner: ['新手友好', 'Beginner'],
    intermediate: ['进阶', 'Intermediate'],
    advanced: ['专家', 'Advanced'],
  }
  const pair = labels[id]
  return pair ? (zh.value ? pair[0] : pair[1]) : id.replaceAll('_', ' ')
}

function abilityMethodLabel(id: string): string {
  const labels: Record<string, [string, string]> = {
    standard_array: ['标准数组', 'Standard array'],
    point_buy: ['27 点购点', '27-point buy'],
    rolled: ['掷骰生成', 'Rolled scores'],
  }
  const pair = labels[id]
  return pair ? (zh.value ? pair[0] : pair[1]) : id.replaceAll('_', ' ')
}

const alignmentOptions = computed(() => [
  ['lawful_good', 'LG', '守序善良', 'Lawful Good'],
  ['neutral_good', 'NG', '中立善良', 'Neutral Good'],
  ['chaotic_good', 'CG', '混乱善良', 'Chaotic Good'],
  ['lawful_neutral', 'LN', '守序中立', 'Lawful Neutral'],
  ['neutral', 'N', '绝对中立', 'True Neutral'],
  ['chaotic_neutral', 'CN', '混乱中立', 'Chaotic Neutral'],
  ...(mode.value === 'expert' ? [
    ['lawful_evil', 'LE', '守序邪恶', 'Lawful Evil'],
    ['neutral_evil', 'NE', '中立邪恶', 'Neutral Evil'],
    ['chaotic_evil', 'CE', '混乱邪恶', 'Chaotic Evil'],
  ] : []),
].map(([value, abbreviation, cn, en]) => ({
  value,
  label: `${abbreviation} · ${zh.value ? cn : en}`,
})))

function optionLabel(id: string): string {
  const labels: Record<string, string> = {
    black: '黑龙', blue: '蓝龙', brass: '黄铜龙', bronze: '青铜龙', copper: '赤铜龙',
    gold: '金龙', green: '绿龙', red: '红龙', silver: '银龙', white: '白龙',
    drow: '卓尔', high_elf: '高等精灵', wood_elf: '木精灵',
    forest_gnome: '森林侏儒', rock_gnome: '岩石侏儒',
    cloud: '云巨人', fire: '火巨人', frost: '霜巨人', hill: '丘陵巨人', stone: '石巨人', storm: '风暴巨人',
    abyssal: '深渊', chthonic: '冥府', infernal: '炼狱', small: '小型', medium: '中型',
  }
  return zh.value ? (labels[id] || abilityName(id)) : id.replaceAll('_', ' ')
}

function setRecommendedAbilities(): void {
  const recommended = choices.value.recommended_base_abilities
  if (Object.keys(recommended).length) draft.value.base_abilities = { ...recommended }
}

function setRecommendedBonuses(): void {
  // Background bonuses are intentionally ranked only within
  // background_ability_refs. Class primary abilities outside this
  // legal set must never be injected here.
  const ids = choices.value.background_ability_refs.map(ref => ref.split(':')[1]).filter(Boolean)
  ids.sort((a, b) => Number(draft.value.base_abilities[b] || 0) - Number(draft.value.base_abilities[a] || 0))
  draft.value.background_ability_bonuses = ids.length >= 2 ? { [ids[0]]: 2, [ids[1]]: 1 } : {}
}

function setSplitBonuses(): void {
  const ids = choices.value.background_ability_refs.map(ref => ref.split(':')[1]).filter(Boolean)
  draft.value.background_ability_bonuses = Object.fromEntries(ids.map(id => [id, 1]))
}

async function selectClass(value: string): Promise<void> {
  draft.value.class_ref = value
  draft.value.class_skill_refs = []
  draft.value.class_tool_refs = []
  draft.value.equipment_package_ref = ''
  draft.value.class_spell_choices = {}
  await refreshChoices()
  setRecommendedAbilities()
  setRecommendedSpells()
  if (draft.value.background_ref) setRecommendedBonuses()
}

function setRecommendedSpells(): void {
  draft.value.class_spell_choices = {
    ...choices.value.recommended_class_spells,
    cantrip_ids: [...(choices.value.recommended_class_spells.cantrip_ids || [])],
    prepared_spell_ids: [...(choices.value.recommended_class_spells.prepared_spell_ids || [])],
    spellbook_ids: [...(choices.value.recommended_class_spells.spellbook_ids || [])],
  }
}

async function completeRecommendedRuleChoices(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    if (choices.value.species_feat_count && !draft.value.species_feat_refs.length) {
      await selectSpeciesFeat(choices.value.species_feats[0]?.ref || '')
    }
    draft.value.class_skill_refs = choices.value.class_skills
      .filter(item => !draft.value.species_skill_refs.includes(item.ref))
      .slice(0, choices.value.class_skill_count)
      .map(item => item.ref)
    draft.value.species_skill_refs = choices.value.species_skills
      .filter(item => !draft.value.class_skill_refs.includes(item.ref))
      .slice(0, choices.value.species_skill_count)
      .map(item => item.ref)
    draft.value.class_tool_refs = choices.value.class_tools
      .slice(0, choices.value.class_tool_count)
      .map(item => item.ref)
    if (!draft.value.species_size && choices.value.species_sizes.length) {
      draft.value.species_size = choices.value.species_sizes[0]
    }
    for (const choice of choices.value.species_choices) {
      if (draft.value.species_choice_answers[choice.id]) continue
      const first = (choice.option_ids || choice.option_refs || [])[0]
      if (first) draft.value.species_choice_answers[choice.id] = first
    }
    const featAnswers = { ...draft.value.feat_choice_answers }
    for (const feat of choices.value.feat_choices) {
      const answers = { ...(featAnswers[feat.feat_ref] || {}) }
      for (const spec of feat.specs) {
        const current = answers[spec.id] || []
        if (current.length === spec.count) continue
        answers[spec.id] = spec.options.slice(0, spec.count).map(option => option.value)
      }
      featAnswers[feat.feat_ref] = answers
    }
    draft.value.feat_choice_answers = featAnswers
    if (!draft.value.equipment_package_ref) {
      draft.value.equipment_package_ref = choices.value.equipment_packages[0]?.ref || ''
    }
    if (!draft.value.background_equipment_package_ref) {
      draft.value.background_equipment_package_ref = choices.value.background_equipment_packages[0]?.ref || ''
    }
    const languages = choices.value.languages.map(item => item.ref)
    const common = languages.includes('language:common') ? ['language:common'] : []
    draft.value.language_refs = [...common, ...languages.filter(refValue => refValue !== 'language:common')].slice(0, 3)
    if (classSpells.value) setRecommendedSpells()
  } finally {
    busy.value = false
  }
}

type SpellListKey = 'cantrip' | 'prepared_spell' | 'spellbook'

function selectedSpellRefs(key: SpellListKey): string[] {
  const choicesValue = draft.value.class_spell_choices
  const refs = choicesValue[`${key}_refs`]
  if (Array.isArray(refs)) return refs
  const ids = choicesValue[`${key}_ids`]
  return Array.isArray(ids) ? ids.map(id => `spell:${id}`) : []
}

function toggleSpell(key: SpellListKey, spell: RulesetSpellChoice, maximum: number): void {
  const current = selectedSpellRefs(key)
  const index = current.indexOf(spell.ref)
  if (index >= 0) current.splice(index, 1)
  else if (current.length < maximum) current.push(spell.ref)
  const next: RulesetSelectedClassSpells = { ...draft.value.class_spell_choices }
  delete next[`${key}_ids`]
  next[`${key}_refs`] = current
  if (key === 'spellbook') {
    next.prepared_spell_refs = selectedSpellRefs('prepared_spell')
      .filter(refValue => current.includes(refValue))
    delete next.prepared_spell_ids
  }
  draft.value.class_spell_choices = next
}

function spellMeta(spell: RulesetSpellChoice): string {
  const level = spell.level === 0 ? text('戏法', 'Cantrip') : text(`${spell.level} 环`, `Level ${spell.level}`)
  const flags = [spell.ritual ? text('仪式', 'Ritual') : '', spell.concentration ? text('专注', 'Concentration') : ''].filter(Boolean)
  return [level, spell.school, ...flags].join(' · ')
}

async function selectBackground(value: string): Promise<void> {
  draft.value.background_ref = value
  draft.value.class_skill_refs = []
  draft.value.species_skill_refs = []
  draft.value.background_equipment_package_ref = ''
  draft.value.feat_choice_answers = {}
  await refreshChoices()
  setRecommendedBonuses()
}

async function selectSpecies(value: string): Promise<void> {
  draft.value.species_ref = value
  draft.value.species_size = undefined
  draft.value.species_choice_answers = {}
  draft.value.species_skill_refs = []
  draft.value.species_feat_refs = []
  draft.value.feat_choice_answers = {}
  await refreshChoices()
  if (choices.value.species_sizes.length === 1) draft.value.species_size = choices.value.species_sizes[0]
}


async function selectSpeciesFeat(value: string): Promise<void> {
  draft.value.species_feat_refs = value ? [value] : []
  draft.value.feat_choice_answers = Object.fromEntries(
    Object.entries(draft.value.feat_choice_answers).filter(([featRef]) => featRef === value),
  )
  await refreshChoices()
}

function refChoiceDisabled(key: RefListKey, refValue: string, max: number): boolean {
  const current = draft.value[key]
  if (current.includes(refValue)) return false
  if (key === 'class_skill_refs' && draft.value.species_skill_refs.includes(refValue)) return true
  if (key === 'species_skill_refs' && draft.value.class_skill_refs.includes(refValue)) return true
  return Boolean(max && current.length >= max)
}

function featChoiceDisabled(featRef: string, choiceId: string, value: string, count: number): boolean {
  if (count <= 1) return false
  const current = draft.value.feat_choice_answers[featRef]?.[choiceId] || []
  return !current.includes(value) && current.length >= count
}

function spellChoiceDisabled(key: SpellListKey, spell: RulesetSpellChoice, maximum: number): boolean {
  const current = selectedSpellRefs(key)
  if (current.includes(spell.ref)) return false
  if (
    key === 'prepared_spell'
    && classSpells.value?.requirements.spellbook_minimum
    && !selectedSpellRefs('spellbook').includes(spell.ref)
  ) return true
  return Boolean(maximum && current.length >= maximum)
}

function toggleRef(key: RefListKey, refValue: string, max: number): void {
  const current = [...draft.value[key]]
  const index = current.indexOf(refValue)
  if (index >= 0) current.splice(index, 1)
  else if (!max || current.length < max) current.push(refValue)
  draft.value[key] = current
}

function setFeatChoice(featRef: string, choiceId: string, value: string, count: number): void {
  const featAnswers = { ...(draft.value.feat_choice_answers[featRef] || {}) }
  const current = [...(featAnswers[choiceId] || [])]
  if (count === 1) {
    featAnswers[choiceId] = [value]
  } else {
    const index = current.indexOf(value)
    if (index >= 0) current.splice(index, 1)
    else if (current.length < count) current.push(value)
    featAnswers[choiceId] = current
  }
  draft.value.feat_choice_answers = {
    ...draft.value.feat_choice_answers,
    [featRef]: featAnswers,
  }
}

async function choosePreset(preset: RulesetQuickCharacterPreset): Promise<void> {
  const name = draft.value.name
  store.draft = {
    ...blankDraft(), ...preset.draft, locale: props.language || props.experience.locale,
    name,
  }
  selectedPreset.value = preset.ref
  error.value = ''
  await refreshChoices()
}

function friendlyErrors(messages: string[]): string {
  const translations: Array<[string, string]> = [
    ['character name is required', '请先给角色起一个名字。'],
    ['character creation currently supports level 1', '高级建卡器用于创建 1 级角色；已有高等级角色不需要重新建卡，请直接关闭并使用该角色，后续等级通过“职业升级”处理。'],
    ['class requires exactly', '职业技能的选择数量还不正确。'],
    ['choose exactly three different languages', '请选择通用语和另外两种不同的标准语言。'],
    ['all characters must know Common', '所有角色都需要掌握通用语。'],
    ['equipment package', '请完成职业与背景的起始装备选择。'],
    ['species choice', '请完成物种带来的额外选择。'],
    ['species skill choices must not duplicate', '物种技能不能与职业或背景技能重复，请取消重复项。'],
    ['species skill', '请完成物种技能选择。'],
    ['species feat', '请完成物种专长选择。'],
    ['requires its guided choices', '请完成起源专长带来的后续选择。'],
    ['feat ', '起源专长的选择还不完整或不合法。'],
    ['class tool', '请完成职业工具选择。'],
    ['class_spell_choices', '请完成职业法术选择。'],
    ['cantrip_refs', '戏法选择数量不正确或含有不合法法术。'],
    ['prepared_spell_refs', '准备法术的数量不正确或含有不合法法术。'],
    ['spellbook_refs', '法师法术书选择不完整，且准备法术必须包含在法术书中。'],
    ['background ability bonuses', '背景属性提升应为 +2/+1，且必须来自该背景列出的三项属性。'],
  ]
  return messages.map(message => {
    if (!zh.value) return message
    return translations.find(([needle]) => message.includes(needle))?.[1] || message
  }).join('\n')
}

async function validateAndPreview(): Promise<boolean> {
  busy.value = true
  error.value = ''
  try {
    const validation = await validateRulesetBuilderDraft(props.ruleId, draft.value, props.language)
    if (!validation.valid) {
      error.value = friendlyErrors(validation.errors)
      return false
    }
    preview.value = (await deriveRulesetBuilderCharacter(
      props.ruleId, draft.value, props.language,
    )).character
    return true
  } catch (cause: unknown) {
    error.value = String((cause as Error)?.message || cause)
    return false
  } finally { busy.value = false }
}

async function next(): Promise<void> {
  error.value = ''
  if (step.value === 1 && (!draft.value.name.trim() || !draft.value.class_ref || !draft.value.species_ref || !draft.value.background_ref)) {
    error.value = text('请先填写名字，并选择职业、物种和背景。', 'Choose a name, class, species, and background first.')
    return
  }
  if (step.value === 2 && ABILITIES.some(key => !Number.isFinite(Number(draft.value.base_abilities[key])))) {
    error.value = text('六项属性还没有填写完整。', 'Complete all six ability scores.')
    return
  }
  if (step.value === 3 && !ruleChoicesReady.value) {
    error.value = text(
      `还差 ${incompleteRuleChoices.value.length} 项，请按上方清单补齐。`,
      `${incompleteRuleChoices.value.length} required choice(s) remain. Complete the checklist above.`,
    )
    return
  }
  if (step.value === 3 && !await validateAndPreview()) return
  step.value = Math.min(4, step.value + 1)
}

async function finish(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const validation = await validateRulesetBuilderDraft(props.ruleId, draft.value, props.language)
    if (!validation.valid) {
      error.value = friendlyErrors(validation.errors)
      return
    }
    const result = await finalizeRulesetBuilderCharacter(props.ruleId, draft.value, props.language)
    store.clear()
    emit('submit', result.character as CharacterSheet, { targetLevel: targetLevel.value })
  } catch (cause: unknown) {
    error.value = String((cause as Error)?.message || cause)
  } finally { busy.value = false }
}

onMounted(async () => {
  store.open(props.ruleId, props.language, draftFromInitial(props.initial) || blankDraft())
  if (props.initial?.ruleset_character) mode.value = 'guided'
  await refreshChoices()
})
</script>

<template>
  <section :class="['ruleset-experience ruleset-experience--dnd2024', { embedded }]" aria-labelledby="dnd-builder-title">
    <header class="dnd-builder-head">
      <div><span class="eyebrow">5E · 2024 · SRD</span><h2 id="dnd-builder-title">{{ text('创建你的冒险者', 'Create your adventurer') }}</h2><p>{{ text('每一步都会检查规则；带“引导”的能力会在游戏中提醒你确认。', 'Every step is rules-checked. Guided features will ask for confirmation in play.') }}</p></div>
      <button type="button" class="close" :aria-label="text('关闭', 'Close')" @click="emit('cancel')">×</button>
    </header>

    <nav class="mode-tabs" role="tablist" :aria-label="text('创建模式', 'Builder mode')">
      <button
        v-for="item in (['quick', 'guided', 'expert'] as BuilderMode[])"
        :id="`builder-mode-${item}`"
        :key="item"
        role="tab"
        aria-controls="builder-mode-panel"
        :aria-selected="mode === item"
        :tabindex="mode === item ? 0 : -1"
        :class="{ active: mode === item }"
        @click="selectMode(item)"
        @keydown="onModeKey"
      >
        {{ item === 'quick' ? text('快速创建', 'Quick') : item === 'guided' ? text('引导创建', 'Guided') : text('专家创建', 'Expert') }}
      </button>
    </nav>

    <div v-if="loading && !choices.classes.length" class="builder-loading" role="status">{{ text('正在整理可用选项…', 'Loading legal choices…') }}</div>

    <main v-else-if="mode === 'quick'" id="builder-mode-panel" class="quick-builder" role="tabpanel" :aria-labelledby="`builder-mode-${mode}`">
      <div class="beginner-callout"><b>{{ text('第一次玩？从这里开始。', 'First game? Start here.') }}</b><span>{{ text('选一个你喜欢的玩法，只需要再填写名字。之后仍可进入引导模式微调。', 'Pick a play style, then add a name. You can still fine-tune it in Guided mode.') }}</span></div>
      <div class="preset-grid">
        <button v-for="preset in choices.quick_presets" :key="preset.ref" :class="['preset-card', { selected: selectedPreset === preset.ref }]" @click="choosePreset(preset)">
          <span class="preset-top"><b>{{ preset.name }}</b><small>{{ difficultyLabel(preset.difficulty) }}</small></span>
          <span>{{ preset.summary }}</span><em>{{ preset.recommendation_reason }}</em>
          <span class="tag-row"><i v-for="tag in preset.fantasy_tags" :key="tag">{{ tag }}</i></span>
        </button>
      </div>
      <label class="name-field"><span>{{ text('角色名', 'Character name') }}</span><input v-model.trim="draft.name" maxlength="100" :placeholder="text('例如：阿岚', 'For example: Arden')"></label>
      <label v-if="!initial" class="name-field start-level-field"><span>{{ text('起始等级', 'Starting level') }}</span><select v-model.number="targetLevel"><option v-for="level in startLevelOptions" :key="level" :value="level">{{ level }}</option></select><small v-if="targetLevel > 1" class="field-help">{{ text(`保存后会连续完成第 2 级到第 ${targetLevel} 级的升级选择。`, `After saving, you will choose levels 2 through ${targetLevel} in a row.`) }}</small></label>
      <p v-if="error" class="builder-error" role="alert">{{ error }}</p>
      <div class="quick-actions"><button @click="mode = 'guided'">{{ text('进入引导模式微调', 'Fine-tune in Guided mode') }}</button><button class="primary" :disabled="busy || !selectedPreset || !draft.name.trim()" @click="finish">{{ busy ? text('检查中…', 'Checking…') : text('完成并使用这个角色', 'Use this character') }}</button></div>
    </main>

    <main v-else id="builder-mode-panel" class="guided-builder" role="tabpanel" :aria-labelledby="`builder-mode-${mode}`">
      <ProgressRail :steps="steps" :current="step" />

      <section v-if="step === 1" class="builder-step">
        <div class="step-intro"><h3>{{ text('先确定你是谁、想怎样冒险', 'Choose who you are and how you adventure') }}</h3><p>{{ text('职业决定主要玩法；物种提供天生特质；背景记录成为冒险者之前的经历。', 'Class sets your core play, species grants innate traits, and background describes your formative life.') }}</p></div>
        <label class="name-field"><span>{{ text('角色名', 'Character name') }}</span><input v-model.trim="draft.name" maxlength="100"></label>
        <label v-if="!initial" class="name-field start-level-field"><span>{{ text('起始等级', 'Starting level') }}</span><select v-model.number="targetLevel"><option v-for="level in startLevelOptions" :key="level" :value="level">{{ level }}</option></select><small v-if="targetLevel > 1" class="field-help">{{ text(`保存后会连续完成第 2 级到第 ${targetLevel} 级的升级选择。`, `After saving, you will choose levels 2 through ${targetLevel} in a row.`) }}</small></label>
        <fieldset><legend>{{ text('职业', 'Class') }}</legend><ChoiceGrid :model-value="draft.class_ref" :choices="choices.classes" :source-visible="sourceVisible" @update:model-value="selectClass" /></fieldset>
        <fieldset><legend>{{ text('物种', 'Species') }}</legend><ChoiceGrid :model-value="draft.species_ref" :choices="choices.species" :source-visible="sourceVisible" @update:model-value="selectSpecies" /></fieldset>
        <fieldset><legend>{{ text('背景', 'Background') }}</legend><ChoiceGrid :model-value="draft.background_ref" :choices="choices.backgrounds" :source-visible="sourceVisible" @update:model-value="selectBackground" /></fieldset>
        <label><span>{{ text('阵营（描述倾向，不限制你的扮演）', 'Alignment (a tendency, not a restriction)') }}</span><select v-model="draft.alignment"><option v-for="item in alignmentOptions" :key="item.value" :value="item.value">{{ item.label }}</option></select><small class="field-help">{{ text('缩写与常见 D&D 资料一致：前一位表示守序／中立／混乱，后一位表示善良／中立／邪恶。', 'These standard D&D abbreviations combine law/neutrality/chaos with good/neutrality/evil.') }}</small></label>
      </section>

      <section v-else-if="step === 2" class="builder-step">
        <div class="step-intro"><h3>{{ text('属性决定你做事时的基础优势', 'Abilities are your basic strengths') }}</h3><p>{{ text('推荐值已经按职业排好。新手直接采用即可；背景带来的 +2/+1 已单独显示。', 'Recommended scores are arranged for your class. Beginners can keep them; background +2/+1 is shown separately.') }}</p></div>
        <fieldset class="base-ability-block"><legend>{{ text('基础属性分配', 'Base ability scores') }}</legend>
          <p class="field-help">{{ text('这里决定 STR / DEX / CON / INT / WIS / CHA 的基础值。27 点购点可以在六项属性之间自由分配，单项范围 8–15。', 'This section sets your six base abilities. 27-point buy can be distributed across all six abilities, with each score ranging from 8 to 15.') }}</p>
          <div class="ability-methods"><label v-for="method in choices.ability_methods" :key="method.id"><input type="radio" v-model="draft.ability_method" :value="method.id" @change="method.id !== 'rolled' && setRecommendedAbilities()"> {{ abilityMethodLabel(method.id) }}</label></div>
          <button v-if="draft.ability_method !== 'rolled'" type="button" class="use-class-recommendation" @click="setRecommendedAbilities">{{ text('使用职业推荐', 'Use class recommendation') }}</button>
          <p v-if="draft.ability_method === 'point_buy'" :class="['point-budget', { invalid: pointBuyRemaining !== 0 }]">{{ text(`购点已使用 ${pointBuySpent} / 27（剩余 ${pointBuyRemaining}）`, `Point buy: ${pointBuySpent} / 27 spent (${pointBuyRemaining} remaining)`) }}</p>
          <div class="ability-grid"><label v-for="key in ABILITIES" :key="key"><span>{{ abilityName(key) }}</span><input type="number" v-model.number="draft.base_abilities[key]" :readonly="draft.ability_method === 'standard_array'" :min="draft.ability_method === 'point_buy' ? 8 : 3" :max="draft.ability_method === 'point_buy' ? 15 : draft.ability_method === 'rolled' ? 18 : 20"><small>+ {{ draft.background_ability_bonuses[key] || 0 }} = <b>{{ Number(draft.base_abilities[key] || 0) + Number(draft.background_ability_bonuses[key] || 0) }}</b></small></label></div>
          <p v-if="recommendedPrimaryAbility" class="field-help">{{ text(`职业推荐：${abilityName(recommendedPrimaryAbility)}优先`, `Class recommendation: prioritize ${abilityName(recommendedPrimaryAbility)}`) }}</p>
        </fieldset>
        <fieldset class="background-bonus-block"><legend>{{ text('背景属性提升', 'Background ability bonuses') }}</legend>
          <p class="field-help">{{ text('背景提供额外的 +2/+1 或 +1/+1/+1。这些加值只能分配到当前背景允许的属性中，不影响你在上方基础属性分配中调整其他属性。', 'Your background grants +2/+1 or +1/+1/+1. These bonuses can only be assigned to the abilities allowed by that background and do not restrict the six base scores above.') }}</p>
          <p v-if="backgroundAllowedIds.length" class="field-help">{{ text(`当前背景可提升：${backgroundAllowedIds.map(abilityName).join(' / ')}`, `Allowed by this background: ${backgroundAllowedIds.map(abilityName).join(' / ')}`) }}</p>
          <div class="bonus-editor"><button type="button" @click="setRecommendedBonuses">{{ text('在背景允许属性中推荐 +2/+1', 'Recommended +2/+1') }}</button><button type="button" @click="setSplitBonuses">{{ text('平均 +1/+1/+1', 'Split +1/+1/+1') }}</button><code v-if="sourceVisible">srd-5.2.1:p21:adjust-ability-scores</code></div>
          <div v-if="mode === 'expert'" class="expert-bonuses"><label v-for="refValue in choices.background_ability_refs" :key="refValue"><span>{{ abilityName(refValue.split(':')[1]) }}</span><select v-model.number="draft.background_ability_bonuses[refValue.split(':')[1]]"><option :value="0">+0</option><option :value="1">+1</option><option :value="2">+2</option></select></label></div>
          <p class="field-help">{{ text('仅在当前背景允许的属性中分配。', 'Only distributed among abilities allowed by the selected background.') }}</p>
          <p v-if="primaryAbilityBlockedByBackground" class="field-help ability-conflict" role="note">{{ text(`⚠ 当前背景无法提升职业推荐主属性「${abilityName(recommendedPrimaryAbility)}」。`, `⚠ This background cannot increase your class's recommended primary ability (${abilityName(recommendedPrimaryAbility)}).`) }}{{ text('这是合法组合，只是协同较弱：你仍然可以在上方基础属性分配中提高该属性；如果希望背景加值也作用于它，可以选择允许提升该属性的背景。', 'This is a legal combination with weaker synergy: you can still raise it in the base ability scores above; pick a different background if you want the background bonus to apply to it.') }}</p>
        </fieldset>
      </section>

      <section v-else-if="step === 3" class="builder-step">
        <div class="step-intro"><h3>{{ text('完成会影响规则的选择', 'Complete rules-relevant choices') }}</h3><p>{{ text('我们只展示当前职业、物种和背景允许的选项，并阻止超选。', 'Only options legal for your current class, species, and background are shown.') }}</p></div>
        <aside class="completion-panel" aria-live="polite">
          <div class="completion-head">
            <div><b>{{ text('创建前完成清单', 'Required choices') }}</b><span>{{ ruleChoicesReady ? text('已全部完成，可以进入审核。', 'Everything is complete. You can continue to review.') : text(`还差 ${incompleteRuleChoices.length} 项；打勾后这里会立刻更新。`, `${incompleteRuleChoices.length} item(s) remain; this list updates immediately.`) }}</span></div>
            <button v-if="!ruleChoicesReady" type="button" :disabled="busy" @click="completeRecommendedRuleChoices">{{ text('帮我用推荐项补全', 'Complete with recommendations') }}</button>
          </div>
          <ul><li v-for="item in ruleChoiceChecklist" :key="item.key" :class="{ done: item.done }"><span>{{ item.done ? '✓' : '○' }}</span>{{ item.label }}</li></ul>
        </aside>
        <fieldset v-if="choices.class_skill_count"><legend>{{ text(`职业技能（${draft.class_skill_refs.length}/${choices.class_skill_count}）`, `Class skills (${draft.class_skill_refs.length}/${choices.class_skill_count})`) }}</legend><div class="check-grid"><label v-for="item in choices.class_skills" :key="item.ref" :class="{ unavailable: refChoiceDisabled('class_skill_refs', item.ref, choices.class_skill_count) }"><input type="checkbox" :checked="draft.class_skill_refs.includes(item.ref)" :disabled="refChoiceDisabled('class_skill_refs', item.ref, choices.class_skill_count)" @change="toggleRef('class_skill_refs', item.ref, choices.class_skill_count)"> {{ item.name }}</label></div></fieldset>
        <fieldset v-if="choices.species_skill_count"><legend>{{ text(`物种技能（${draft.species_skill_refs.length}/${choices.species_skill_count}）`, `Species skill (${draft.species_skill_refs.length}/${choices.species_skill_count})`) }}</legend><div class="check-grid"><label v-for="item in choices.species_skills" :key="item.ref" :class="{ unavailable: refChoiceDisabled('species_skill_refs', item.ref, choices.species_skill_count) }"><input type="checkbox" :checked="draft.species_skill_refs.includes(item.ref)" :disabled="refChoiceDisabled('species_skill_refs', item.ref, choices.species_skill_count)" @change="toggleRef('species_skill_refs', item.ref, choices.species_skill_count)"> {{ item.name }}</label></div></fieldset>
        <fieldset v-if="choices.species_feat_count"><legend>{{ text('物种专长', 'Species feat') }}</legend><ChoiceGrid :model-value="draft.species_feat_refs[0]" :choices="choices.species_feats" compact :source-visible="sourceVisible" @update:model-value="selectSpeciesFeat" /></fieldset>
        <fieldset v-if="choices.class_tool_count"><legend>{{ text(`职业工具（${draft.class_tool_refs.length}/${choices.class_tool_count}）`, `Class tools (${draft.class_tool_refs.length}/${choices.class_tool_count})`) }}</legend><div class="check-grid"><label v-for="item in choices.class_tools" :key="item.ref" :class="{ unavailable: refChoiceDisabled('class_tool_refs', item.ref, choices.class_tool_count) }"><input type="checkbox" :checked="draft.class_tool_refs.includes(item.ref)" :disabled="refChoiceDisabled('class_tool_refs', item.ref, choices.class_tool_count)" @change="toggleRef('class_tool_refs', item.ref, choices.class_tool_count)"> {{ item.name }}</label></div></fieldset>
        <fieldset v-if="choices.species_sizes.length > 1"><legend>{{ text('体型', 'Size') }}</legend><div class="check-grid"><label v-for="size in choices.species_sizes" :key="size"><input type="radio" v-model="draft.species_size" :value="size"> {{ optionLabel(size) }}</label></div></fieldset>
        <fieldset v-for="choice in choices.species_choices" :key="choice.id"><legend>{{ optionLabel(choice.id) }}</legend><select :value="draft.species_choice_answers[choice.id] || ''" @change="draft.species_choice_answers[choice.id] = ($event.target as HTMLSelectElement).value"><option value="" disabled>{{ text('请选择', 'Choose') }}</option><option v-for="option in (choice.option_ids || choice.option_refs || [])" :key="option" :value="option">{{ option.includes(':') ? choiceName(option) : optionLabel(option) }}</option></select></fieldset>
        <fieldset v-for="feat in choices.feat_choices" :key="feat.feat_ref" class="feat-choices"><legend>{{ feat.name }} · {{ text('后续选择', 'Required choices') }}</legend><p>{{ feat.summary }}</p><div v-for="spec in feat.specs" :key="spec.id" class="feat-choice-group"><b>{{ spec.name }}</b><div class="check-grid"><label v-for="option in spec.options" :key="option.value" :class="{ unavailable: featChoiceDisabled(feat.feat_ref, spec.id, option.value, spec.count) }"><input :type="spec.count === 1 ? 'radio' : 'checkbox'" :name="`${feat.feat_ref}-${spec.id}`" :checked="(draft.feat_choice_answers[feat.feat_ref]?.[spec.id] || []).includes(option.value)" :disabled="featChoiceDisabled(feat.feat_ref, spec.id, option.value, spec.count)" @change="setFeatChoice(feat.feat_ref, spec.id, option.value, spec.count)"> {{ option.name }}</label></div><small>{{ text(`已选 ${(draft.feat_choice_answers[feat.feat_ref]?.[spec.id] || []).length}/${spec.count}`, `Selected ${(draft.feat_choice_answers[feat.feat_ref]?.[spec.id] || []).length}/${spec.count}`) }}</small></div><code v-if="sourceVisible">{{ feat.source_ref }}</code></fieldset>
        <fieldset v-if="classSpells" class="class-spells"><legend>{{ text('职业法术', 'Class spells') }}</legend>
          <div class="spell-toolbar"><p>{{ text('只列出该职业在当前等级可合法选择的法术。推荐组合可以直接使用，也可以逐项替换。', 'Only spells legal for this class and level are listed. Keep the recommended set or replace individual choices.') }}</p><button type="button" @click="setRecommendedSpells">{{ text('恢复推荐组合', 'Restore recommendations') }}</button></div>
          <div v-if="classSpells.requirements.cantrip_count" class="spell-group"><b>{{ text(`戏法（${selectedSpellRefs('cantrip').length} / ${classSpells.requirements.cantrip_count}）`, `Cantrips (${selectedSpellRefs('cantrip').length} / ${classSpells.requirements.cantrip_count})`) }}</b><div class="spell-grid"><label v-for="spell in classSpells.cantrips" :key="spell.ref" :class="{ picked: selectedSpellRefs('cantrip').includes(spell.ref), unavailable: spellChoiceDisabled('cantrip', spell, classSpells.requirements.cantrip_count) }"><input type="checkbox" :checked="selectedSpellRefs('cantrip').includes(spell.ref)" :disabled="spellChoiceDisabled('cantrip', spell, classSpells.requirements.cantrip_count)" @change="toggleSpell('cantrip', spell, classSpells.requirements.cantrip_count)"><span><strong>{{ spell.name }}</strong><small>{{ spellMeta(spell) }}</small></span></label></div></div>
          <label class="spell-search"><span>{{ text('搜索可选法术', 'Search eligible spells') }}</span><input v-model.trim="spellSearch" type="search" :placeholder="text('名称或学派', 'Name or school')"></label>
          <div v-if="classSpells.requirements.spellbook_minimum" class="spell-group"><b>{{ text(`法术书（${selectedSpellRefs('spellbook').length} / ${classSpells.requirements.spellbook_minimum}）`, `Spellbook (${selectedSpellRefs('spellbook').length} / ${classSpells.requirements.spellbook_minimum})`) }}</b><div class="spell-grid"><label v-for="spell in visibleLeveledSpells" :key="`book-${spell.ref}`" :class="{ picked: selectedSpellRefs('spellbook').includes(spell.ref), unavailable: spellChoiceDisabled('spellbook', spell, classSpells.requirements.spellbook_minimum) }"><input type="checkbox" :checked="selectedSpellRefs('spellbook').includes(spell.ref)" :disabled="spellChoiceDisabled('spellbook', spell, classSpells.requirements.spellbook_minimum)" @change="toggleSpell('spellbook', spell, classSpells.requirements.spellbook_minimum)"><span><strong>{{ spell.name }}</strong><small>{{ spellMeta(spell) }}</small></span></label></div></div>
          <div v-if="classSpells.requirements.prepared_spell_count" class="spell-group"><b>{{ text(`准备法术（${selectedSpellRefs('prepared_spell').length} / ${classSpells.requirements.prepared_spell_count}）`, `Prepared spells (${selectedSpellRefs('prepared_spell').length} / ${classSpells.requirements.prepared_spell_count})`) }}</b><p v-if="classSpells.requirements.spellbook_minimum">{{ text('法师只能从已经写入法术书的法术中准备。', 'A Wizard prepares only spells already recorded in the spellbook.') }}</p><div class="spell-grid"><label v-for="spell in visibleLeveledSpells" :key="`prepared-${spell.ref}`" :class="{ picked: selectedSpellRefs('prepared_spell').includes(spell.ref), unavailable: spellChoiceDisabled('prepared_spell', spell, classSpells.requirements.prepared_spell_count) }"><input type="checkbox" :disabled="spellChoiceDisabled('prepared_spell', spell, classSpells.requirements.prepared_spell_count)" :checked="selectedSpellRefs('prepared_spell').includes(spell.ref)" @change="toggleSpell('prepared_spell', spell, classSpells.requirements.prepared_spell_count)"><span><strong>{{ spell.name }}</strong><small>{{ spellMeta(spell) }}</small></span></label></div></div>
          <code v-if="sourceVisible">srd-5.2.1:p99:spell-lists</code>
        </fieldset>
        <fieldset><legend>{{ text('职业起始装备', 'Class starting equipment') }}</legend><ChoiceGrid v-model="draft.equipment_package_ref" :choices="choices.equipment_packages" compact :source-visible="sourceVisible" /></fieldset>
        <fieldset><legend>{{ text('背景起始装备', 'Background starting equipment') }}</legend><ChoiceGrid v-model="draft.background_equipment_package_ref" :choices="choices.background_equipment_packages" compact :source-visible="sourceVisible" /></fieldset>
        <fieldset><legend>{{ text(`语言（${draft.language_refs.length}/3，含通用语）`, `Languages (${draft.language_refs.length}/3, including Common)`) }}</legend><div class="check-grid"><label v-for="item in choices.languages" :key="item.ref" :class="{ unavailable: refChoiceDisabled('language_refs', item.ref, 3) }"><input type="checkbox" :checked="draft.language_refs.includes(item.ref)" :disabled="item.ref === 'language:common' || refChoiceDisabled('language_refs', item.ref, 3)" @change="toggleRef('language_refs', item.ref, 3)"> {{ item.name }}</label></div></fieldset>
      </section>

      <section v-else class="builder-step review-step">
        <div class="step-intro"><h3>{{ text('角色已通过规则检查', 'Your character passed rules validation') }}</h3><p>{{ text('下面的数值由服务端根据选择重新计算，不采用浏览器上传的派生值。', 'These values are recomputed by the server from your choices, never trusted from the browser.') }}</p></div>
        <div class="review-hero"><div><b>{{ draft.name }}</b><span>{{ choiceName(draft.species_ref) }} · {{ choiceName(draft.class_ref) }} · {{ choiceName(draft.background_ref) }}</span></div><strong>Lv. {{ draft.level }}</strong></div>
        <div class="derived-grid"><article><span>HP</span><b>{{ previewResources.max_hp }}</b></article><article><span>AC</span><b>{{ previewDerived.armor_class }}</b></article><article><span>{{ text('先攻', 'Initiative') }}</span><b>{{ previewDerived.initiative }}</b></article><article><span>{{ text('速度', 'Speed') }}</span><b>{{ previewDerived.speed }} ft</b></article><article><span>{{ text('被动察觉', 'Passive Perception') }}</span><b>{{ previewDerived.passive_perception }}</b></article><article><span>{{ text('熟练加值', 'Proficiency') }}</span><b>+{{ previewDerived.proficiency_bonus }}</b></article></div>
        <details v-if="sourceVisible"><summary>{{ text('查看 canonical 预览', 'View canonical preview') }}</summary><pre>{{ JSON.stringify(preview, null, 2) }}</pre></details>
      </section>

      <p v-if="error" class="builder-error" role="alert">{{ error }}</p>
      <footer class="builder-actions"><button @click="emit('cancel')">{{ text('取消', 'Cancel') }}</button><button v-if="step > 1" @click="step--">{{ text('上一步', 'Back') }}</button><button v-if="step < 4" class="primary" :disabled="busy || (step === 3 && !ruleChoicesReady)" @click="next">{{ busy ? text('检查中…', 'Checking…') : step === 3 && !ruleChoicesReady ? text(`还差 ${incompleteRuleChoices.length} 项`, `${incompleteRuleChoices.length} remaining`) : text('下一步', 'Next') }}</button><button v-else class="primary" :disabled="busy" @click="finish">{{ busy ? text('正在完成…', 'Finalizing…') : text('完成角色', 'Finish character') }}</button></footer>
    </main>
  </section>
</template>

<style scoped>
.ruleset-experience--dnd2024 { width: min(1120px, 100%); max-height: min(920px, calc(100vh - 36px)); overflow: auto; border: 1px solid var(--df-border); border-radius: 22px; background: radial-gradient(circle at 88% 0%, color-mix(in srgb, var(--df-accent) 14%, transparent), transparent 32%), var(--df-surface-1); color: var(--df-text); box-shadow: var(--df-shadow-strong); }
.ruleset-experience--dnd2024.embedded { max-height: none; border-radius: 18px; }
.dnd-builder-head { display: flex; justify-content: space-between; gap: 18px; padding: 24px 26px 18px; border-bottom: 1px solid var(--df-border-soft); }
.dnd-builder-head h2 { margin: 3px 0 5px; font-family: Georgia, serif; font-size: clamp(24px, 4vw, 38px); }
.dnd-builder-head p { margin: 0; color: var(--df-text-muted); }
.eyebrow { color: var(--df-accent-strong); font-size: 11px; letter-spacing: .18em; }
.close { width: 44px; height: 44px; border-radius: 50%; font-size: 24px; }
.mode-tabs { display: flex; gap: 8px; padding: 12px 26px; border-bottom: 1px solid var(--df-border-soft); }
.mode-tabs button.active { border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 15%, var(--df-surface-2)); color: var(--df-accent-strong); }
.quick-builder, .guided-builder { display: grid; gap: 20px; padding: 24px 26px 28px; }
.beginner-callout { display: grid; gap: 4px; padding: 14px 16px; border-left: 3px solid var(--df-accent); background: color-mix(in srgb, var(--df-accent) 8%, var(--df-surface-2)); color: var(--df-text-secondary); }
.beginner-callout b { color: var(--df-accent-strong); }
.preset-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(245px, 1fr)); gap: 12px; }
.preset-card { display: grid; gap: 9px; padding: 16px; border: 1px solid var(--df-border-soft); border-radius: 15px; background: var(--df-surface-2); color: var(--df-text-secondary); text-align: left; }
.preset-card.selected { border-color: var(--df-interactive); box-shadow: 0 0 0 2px var(--df-focus); }
.preset-top { display: flex; justify-content: space-between; gap: 12px; color: var(--df-accent-strong); }
.preset-card em { color: var(--df-text-muted); font-size: 12px; line-height: 1.5; }
.tag-row { display: flex; flex-wrap: wrap; gap: 5px; }.tag-row i { padding: 2px 7px; border-radius: 99px; background: var(--df-surface-raised); color: var(--df-text-muted); font-size: 10px; font-style: normal; }
.name-field { display: grid; gap: 7px; max-width: 440px; }.name-field input { font-size: 17px; }.start-level-field select { max-width: 120px; }
.quick-actions, .builder-actions { display: flex; justify-content: flex-end; gap: 9px; }
.builder-step { display: grid; gap: 20px; }.step-intro h3 { margin: 0 0 5px; font-family: var(--df-font-title); font-size: 23px; }.step-intro p { margin: 0; color: var(--df-text-muted); }
fieldset { display: grid; gap: 10px; margin: 0; padding: 14px; border: 1px solid var(--df-border-soft); border-radius: 14px; } legend { padding: 0 7px; color: var(--df-accent-strong); font-weight: 700; }
label { display: grid; gap: 6px; color: var(--df-text-secondary); } input, select { min-height: 44px; border: 1px solid var(--df-border-soft); border-radius: 9px; background: var(--df-control-bg); color: var(--df-text); padding: 8px 10px; }
.field-help { color: var(--df-text-muted); font-size: 12px; line-height: 1.5; }
.ability-methods, .check-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; }.ability-methods label, .check-grid label { display: flex; align-items: center; gap: 8px; min-height: 44px; padding: 8px; border-radius: 8px; background: var(--df-surface-2); }.ability-methods input, .check-grid input { width: 22px; height: 22px; min-height: 22px; }
.check-grid label.unavailable { opacity: .5; cursor: not-allowed; }
.ability-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 9px; }.ability-grid label { padding: 12px; border: 1px solid var(--df-border-soft); border-radius: 12px; text-align: center; }.ability-grid input { width: 100%; font-size: 19px; text-align: center; }.ability-grid small { color: var(--df-text-muted); }
.bonus-editor { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; color: var(--df-text-muted); }
.completion-panel { display: grid; gap: 12px; padding: 14px; border: 1px solid color-mix(in srgb, var(--df-accent) 42%, var(--df-border-soft)); border-radius: 14px; background: color-mix(in srgb, var(--df-accent) 8%, var(--df-surface-2)); }
.completion-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.completion-head div { display: grid; gap: 4px; }.completion-head span { color: var(--df-text-muted); font-size: 12px; }
.completion-panel ul { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 7px; margin: 0; padding: 0; list-style: none; }.completion-panel li { display: flex; gap: 7px; color: var(--df-accent-strong); }.completion-panel li.done { color: var(--df-success-strong); }
.point-budget { margin: 0; color: var(--df-success-strong); }.point-budget.invalid { color: var(--df-warning); }.expert-bonuses { display: grid; grid-template-columns: repeat(3, minmax(120px, 1fr)); gap: 9px; }.feat-choices p { margin: 0; color: var(--df-text-muted); }.feat-choice-group { display: grid; gap: 7px; }
.class-spells, .spell-group { display: grid; gap: 12px; }.spell-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.spell-toolbar p, .spell-group p { margin: 0; color: var(--df-text-muted); }.spell-search { max-width: 420px; }.spell-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 8px; }.spell-grid label { display: flex; align-items: flex-start; gap: 8px; min-height: 44px; padding: 9px; border: 1px solid var(--df-border-soft); border-radius: 9px; background: var(--df-surface-2); }.spell-grid label.picked { border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 12%, var(--df-surface-2)); }.spell-grid label.unavailable { opacity: .5; }.spell-grid label input { width: 22px; height: 22px; min-height: 22px; margin-top: 3px; }.spell-grid label span { display: grid; gap: 2px; }.spell-grid small { color: var(--df-text-muted); }
.builder-error { white-space: pre-line; padding: 12px 14px; border: 1px solid var(--df-danger); border-radius: 10px; background: color-mix(in srgb, var(--df-danger) 16%, var(--df-surface-2)); color: var(--df-danger-strong); }
.review-hero { display: flex; justify-content: space-between; padding: 18px; border: 1px solid color-mix(in srgb, var(--df-accent) 48%, var(--df-border-soft)); border-radius: 15px; background: color-mix(in srgb, var(--df-accent) 10%, var(--df-surface-2)); }.review-hero div { display: grid; gap: 4px; }.review-hero b { font-family: var(--df-font-title); font-size: 25px; }.review-hero span { color: var(--df-text-muted); }.review-hero strong { color: var(--df-accent-strong); }
.derived-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 9px; }.derived-grid article { display: grid; gap: 3px; padding: 13px; border: 1px solid var(--df-border-soft); border-radius: 11px; text-align: center; }.derived-grid span { color: var(--df-text-muted); font-size: 11px; }.derived-grid b { font-size: 20px; }
pre { max-height: 280px; overflow: auto; padding: 12px; background: var(--df-canvas); color: var(--df-text-secondary); font-size: 11px; }
button { min-height: 44px; border: 1px solid var(--df-border-soft); border-radius: 9px; background: var(--df-surface-2); color: var(--df-text); padding: 8px 13px; cursor: pointer; } button.primary { border-color: var(--df-interactive); background: var(--df-interactive); color: var(--df-on-accent); } button:disabled { opacity: .48; cursor: not-allowed; }
button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible { outline: 3px solid var(--df-interactive); outline-offset: 2px; }
:global(body.light .ruleset-experience--dnd2024) { border-color: var(--df-border); background: radial-gradient(circle at 88% 0%, color-mix(in srgb, var(--df-accent) 10%, transparent), transparent 32%), var(--df-surface-1); color: var(--df-text); box-shadow: var(--df-shadow-strong); }
:global(body.light .ruleset-experience--dnd2024 .dnd-builder-head), :global(body.light .ruleset-experience--dnd2024 .mode-tabs) { border-color: var(--df-border-soft); }
:global(body.light .ruleset-experience--dnd2024 .dnd-builder-head p), :global(body.light .ruleset-experience--dnd2024 .step-intro p), :global(body.light .ruleset-experience--dnd2024 .spell-toolbar p), :global(body.light .ruleset-experience--dnd2024 .spell-group p) { color: var(--df-text-muted); }
:global(body.light .ruleset-experience--dnd2024 .preset-card), :global(body.light .ruleset-experience--dnd2024 .ability-grid label), :global(body.light .ruleset-experience--dnd2024 fieldset), :global(body.light .ruleset-experience--dnd2024 .derived-grid article) { border-color: var(--df-border-soft); background: var(--df-surface-raised); color: var(--df-text); }
:global(body.light .ruleset-experience--dnd2024 input), :global(body.light .ruleset-experience--dnd2024 select) { border-color: var(--df-border-soft); background: var(--df-control-bg); color: var(--df-text); }
:global(body.light .ruleset-experience--dnd2024 .ability-methods label), :global(body.light .ruleset-experience--dnd2024 .check-grid label), :global(body.light .ruleset-experience--dnd2024 .spell-grid label) { background: var(--df-surface-2); color: var(--df-text-secondary); }
:global(body.light .ruleset-experience--dnd2024 .completion-panel) { border-color: var(--df-border-soft); background: color-mix(in srgb, var(--df-accent) 8%, var(--df-surface-raised)); }:global(body.light .ruleset-experience--dnd2024 .completion-head span) { color: var(--df-text-muted); }
@media (max-width: 760px) { .ruleset-experience--dnd2024 { max-height: 100dvh; border-radius: 0; }.dnd-builder-head, .quick-builder, .guided-builder { padding-left: 15px; padding-right: 15px; }.mode-tabs { padding-left: 15px; padding-right: 15px; overflow-x: auto; }.ability-grid, .derived-grid { grid-template-columns: repeat(3, 1fr); }.quick-actions, .builder-actions { position: sticky; bottom: 0; z-index: 2; padding: 10px 0 max(10px, env(safe-area-inset-bottom)); background: color-mix(in srgb, var(--df-surface-1) 94%, transparent); }.preset-grid { grid-template-columns: 1fr; } }
@media (max-width: 390px) { .ability-grid, .derived-grid { grid-template-columns: repeat(2, 1fr); }.dnd-builder-head p { font-size: 12px; }.mode-tabs button { white-space: nowrap; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; } }
</style>
