<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import type { AdventureSummary, AdventuresResponse, CharacterCard, CharacterCardsResponse, CharacterSheet, GameMutationResponse, GeneratedRuleResponse, GeneratedWorldResponse, GmStyle, RuleDetailResponse, RuleSummary, RuleTemplate, RulesResponse, SceneImageRef, WorldListResponse, WorldSummary, WorldTemplateSummary, WorldTemplatesResponse } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useLocale, type Locale } from '@/composables/useLocale'
import CharacterWizard from '@/components/admin/CharacterWizard.vue'
import CharacterCardPicker from '@/components/admin/CharacterCardPicker.vue'
import PortraitImage from '@/components/PortraitImage.vue'
import AdventureSceneImagePicker from '@/components/common/AdventureSceneImagePicker.vue'
import MapBackgroundPicker from '@/components/common/MapBackgroundPicker.vue'
import RulesetExperienceHost from '@/features/rulesets/RulesetExperienceHost.vue'
import { importTavernCard } from '@/utils/characterImport'
import { rememberCurrentGame } from '@/stores/gameContext'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { filterByContentLanguage } from '@/utils/contentLanguage'
import { recommendedRuleSummaries } from '@/utils/recommendedRules'
import {
  characterCardHasCompatibleProfessionalBlueprint,
  characterCardNeedsConversion,
} from '@/utils/characterCards'
import { ruleSceneUrl } from '@/composables/useBackgroundImages'
import { resolveSceneImageUrl, revokeSceneImageUrl, sceneImageStyle, uploadSceneImage } from '@/api/sceneImages'
import { mapBackgroundSelection, uploadMapBackground } from '@/api/mapBackgrounds'
import { isLlmConfigReady } from '@/utils/modelConfiguration'
import { moduleApi, type ModuleSummary } from '@/api/modules'
import {
  cardControlAt,
  cycleCardControl,
  removeCardControl,
  syncCardControls,
  type CardControlMode,
} from '@/features/create/cardControl'

interface CreateCharacter extends CharacterSheet { character_name: string }
type CreateMode = 'template' | 'custom' | 'ai'
type Step = 1 | 2 | 3 | 4
type NarrativePerspective = 'immersive' | 'third_person'
type PlayMode = 'free' | 'adventure'
type AdvancementMode = 'milestone' | 'xp'
type AdvancementAuthority = 'ai_gm' | 'gm'
const DIFFICULTY_EASY = '\u8f7b\u677e'
const DIFFICULTY_NORMAL = '\u6807\u51c6'
const DIFFICULTY_HARDCORE = '\u786c\u6838'
const DEFAULT_ADVENTURER_ZH = '\u5192\u9669\u8005'
const DEFAULT_NEW_ADVENTURE_ZH = '\u65b0\u5192\u9669'
const DEFAULT_MY_ADVENTURE_ZH = '\u6211\u7684\u5192\u9669'
const DEFAULT_AI_WORLD_ZH = 'AI \u751f\u6210\u7684\u4e16\u754c'
const BLANK_LOREBOOK_SUFFIX_ZH = '\uff08\u7a7a\u767d\u4e16\u754c\u4e66\uff09'
const COPIED_LOREBOOK_SUFFIX_ZH = '\uff08\u590d\u5236\u4e16\u754c\u4e66\uff09'
const DEFAULT_ADVENTURER_DE = 'Abenteurer'
const DEFAULT_NEW_ADVENTURE_DE = 'Neues Abenteuer'
const DEFAULT_MY_ADVENTURE_DE = 'Mein Abenteuer'
const DEFAULT_AI_WORLD_DE = 'KI-generierte Welt'
const BLANK_LOREBOOK_SUFFIX_DE = ' (Leeres Weltenbuch)'
const COPIED_LOREBOOK_SUFFIX_DE = ' (Kopiertes Weltenbuch)'

const router = useRouter()
const route = useRoute()
const adventureManagerHref = computed(() => router.resolve({ name: 'adventures' }).href)
const toast = useToast()
const { locale, t } = useLocale()
const settings = useSettingsStore()

const worlds = ref<WorldTemplateSummary[]>([])
const rules = ref<RuleSummary[]>([])
const loreWorlds = ref<WorldSummary[]>([])
const mode = ref<CreateMode>('template')
const world = ref(''), rule = ref(''), name = ref(''), description = ref('')
const difficulty = ref(DIFFICULTY_NORMAL), solo = ref(true), roomPassword = ref(''), openRoom = ref(false)
const narrativePerspective = ref<NarrativePerspective>('immersive')
const narrativePerspectiveTouched = ref(false)
const playMode = ref<PlayMode>('free')
const gmStyleFollowWorld = ref(true)
const gmStyle = ref<GmStyle>({ tone: '', verbosity: 'normal', pace: 'normal', custom_instructions: '' })
const advancementMode = ref<AdvancementMode>('milestone')
const advancementAuthority = ref<AdvancementAuthority>('ai_gm')
const gameLanguage = ref<Locale>(locale.value)
const customName = ref(''), customDesc = ref('')
const aiPrompt = ref(''), aiRule = ref('')
const aiAutoRule = ref(false), aiGeneratedRule = ref<GeneratedRuleResponse | null>(null)
const loreChoice = ref('__builtin__')
const adventures = ref<AdventureSummary[]>([])
const adventureId = ref('')
const modules = ref<ModuleSummary[]>([])
const moduleId = ref('')
const moduleAdventureIds = ref<string[]>([])
const seed = ref(''), busy = ref(false), error = ref('')
const settingsChecked = ref(false)
const sceneImageFile = ref<File | null>(null)
const mapBackgroundChoice = ref('auto')
const mapBackgroundFile = ref<File | null>(null)
const defaultSceneImageUrl = ref(ruleSceneUrl())
const customSceneImageUrl = ref('')

const ruleDetail = ref<RuleTemplate | null>(null)
const characters = ref<CreateCharacter[]>([])
const cards = ref<CharacterCard[]>([])
const showWizard = ref(false), showRulesetBuilder = ref(false), showPicker = ref(false)
const editIdx = ref<number | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const dfInput = ref<HTMLInputElement | null>(null)

const step = ref<Step>(1)
const stepTitleKeys = ['stepWorld', 'stepGameSettings', 'stepCharacters', 'stepConfirm'] as const
const activeRule = computed(() => mode.value === 'ai' ? (aiGeneratedRule.value?.rule_id || aiRule.value) : rule.value)
const activeRuleSummary = computed(() => rules.value.find(item => item.rule_id === activeRule.value))
const usesProfessionalBuilder = computed(() => (
  activeRuleSummary.value?.ruleset_runtime?.capabilities.character_builder === 'professional'
))
const supportsAdventurePackages = computed(() => Boolean(
  activeRuleSummary.value?.ruleset_runtime?.capabilities.adventure_formats?.length,
))
const showAdventurePackages = computed(() => (
  mode.value === 'template'
  && supportsAdventurePackages.value
))
const selectedAdventure = computed(() => adventures.value.find(item => item.adventure_id === adventureId.value))
const availableModuleAdventures = computed(() => moduleId.value
  ? adventures.value.filter(item => moduleAdventureIds.value.includes(item.adventure_id))
  : adventures.value)

function selectPlayMode(value: PlayMode): void {
  playMode.value = value
  if (value === 'free') adventureId.value = ''
}
const activeWorldTemplate = computed(() => worlds.value.find(item => worldIdOf(item) === world.value))
const defaultSceneImageRef = computed<SceneImageRef | undefined>(() => {
  if (mode.value === 'template' && activeWorldTemplate.value?.scene_image) return activeWorldTemplate.value.scene_image
  return activeRuleSummary.value?.scene_image || ruleDetail.value?.scene_image
})
const selectedSceneImageUrl = computed(() => customSceneImageUrl.value || defaultSceneImageUrl.value || ruleSceneUrl(activeRule.value))
const availableWorlds = computed(() => worlds.value)
const availableLoreWorlds = computed(() => filterByContentLanguage(loreWorlds.value, gameLanguage.value))
const ruleAttrs = computed(() => ruleDetail.value?.attributes || [])
const skillPool = computed(() => ruleDetail.value?.skill_pool || ruleDetail.value?.skills || [])
const attrTotal = computed(() => ruleDetail.value?.attribute_points || 60)
const selectedTemplateWorldName = computed(() => worldNameOf(worlds.value.find(item => worldIdOf(item) === world.value) || {}))
const recommendedRulesList = computed(() => recommendedRuleSummaries(activeWorldTemplate.value, rules.value))
function isProfessionalRule(summary: RuleSummary): boolean {
  return summary.ruleset_runtime?.capabilities.character_builder === 'professional'
}
function isDndAdvancedRule(summary: RuleSummary): boolean {
  return summary.rule_id === 'dnd2024_srd' || summary.ruleset_runtime?.id === 'core:dnd2024'
}
const supportsAdvancementPolicy = computed(() => Boolean(
  activeRuleSummary.value && isDndAdvancedRule(activeRuleSummary.value),
))
function selectNarrativePerspective(value: NarrativePerspective): void {
  narrativePerspectiveTouched.value = true
  narrativePerspective.value = value
}
function recommendationBadge(summary: RuleSummary): string {
  return t(isProfessionalRule(summary) ? 'professional' : 'recommended')
}
const confirmationName = computed(() => {
  if (seed.value.trim()) return t('restoreBySeed')
  if (mode.value === 'template') return name.value.trim() || selectedTemplateWorldName.value || t('modeTemplate')
  if (mode.value === 'custom') return customName.value.trim() || t('modeCustom')
  return t('modeAi')
})
const confirmationWorld = computed(() => {
  if (seed.value.trim()) return t('restoreBySeed')
  if (mode.value === 'template') return selectedTemplateWorldName.value || world.value
  if (mode.value === 'custom') return customName.value.trim() || t('modeCustom')
  return t('modeAi')
})
const apiReady = computed(() => isLlmConfigReady(settings.config))
const showApiSetupHint = computed(() => settingsChecked.value && !settings.error && !apiReady.value)

function worldIdOf(w: WorldTemplateSummary | WorldSummary): string { return String(w.world_id || w.id || '') }
function worldNameOf(w: WorldTemplateSummary | WorldSummary): string { return String(w.world_name || w.name || w.id || '') }
function worldLanguageLabel(w: WorldTemplateSummary | WorldSummary): string {
  const language = String((w as WorldTemplateSummary).active_locale || (w as WorldSummary).language || '').toLowerCase()
  if (language.startsWith('ja')) return '日本語'
  if (language.startsWith('de')) return t('german')
  if (language.startsWith('en')) return t('english')
  return t('chinese')
}
function worldOptionLabel(w: WorldTemplateSummary | WorldSummary): string { return `${worldNameOf(w)} · ${worldLanguageLabel(w)}` }
function ruleNameOf(r: RuleSummary): string { return String(r.rule_name || r.rule_id) }
function ruleDescriptionOf(r: RuleSummary): string { return String(r.description || '') }
function cloneCharacter<T extends CharacterSheet>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }
function gameDefault(zh: string, en: string, de?: string): string {
  if (gameLanguage.value === 'de') return de ?? en
  return gameLanguage.value === 'en' ? en : zh
}
function stepTitle(value: number): string { return t(stepTitleKeys[value - 1] || 'stepConfirm') }
function ensureCharacter(value: CharacterSheet): CreateCharacter {
  return { ...value, character_name: String(value.character_name || gameDefault(DEFAULT_ADVENTURER_ZH, 'Adventurer', DEFAULT_ADVENTURER_DE)) }
}

// 每张角色卡由谁负责：控制方式在「角色」步骤直接选，确认页只做摘要。
// 默认第一张是「玩家」、其余「等待认领」——与后端三态契约（human / ai / unclaimed）一致。
const cardControl = ref<CardControlMode[]>(['human'])
function controlLabel(mode: string): string {
  if (mode === 'ai') return t('controlAi')
  if (mode === 'unclaimed') return t('controlUnclaimed')
  return t('controlHuman')
}
/** 角色步骤与确认页共用同一个答案，避免确认页显示与刚才选择不同的值。 */
function controlOf(index: number): CardControlMode {
  return cardControlAt(cardControl.value, index)
}
/** 单个循环按钮：点一下换下一个控制方式（玩家 → AI 托管 → 等待认领 → 玩家）。 */
function cycleControl(index: number) {
  cardControl.value = cycleCardControl(cardControl.value, index)
}
/** 按钮只显示当前状态，所以用 title / aria-label 说明「点它会切换」。 */
function controlSwitchHint(index: number): string {
  return t('controlSwitchHint', { mode: controlLabel(controlOf(index)) })
}
// 任何进入 characters[] 的路径（手动创建 / 角色卡选择器 / 导入 / 专业建卡）都会
// 经过这里补齐控制方式，不会出现「导入的角色没有控制方式」。
watch(() => characters.value.length, (length) => {
  const next = syncCardControls(length, cardControl.value)
  if (next.length !== cardControl.value.length || next.some((v, i) => v !== cardControl.value[i])) cardControl.value = next
}, { immediate: true })

function legacyCharacterFromCard(card: CharacterCard): CreateCharacter {
  return ensureCharacter({
    character_name: card.character_name,
    background: card.background || '',
    identity: card.identity || {},
    attributes: card.attributes || {},
    skills: card.skills || [],
    equipment: card.equipment || [],
    inventory: card.inventory || [],
    key_items: card.key_items || [],
    gold: card.gold || 0,
    currency: card.currency,
    race: card.race,
    class: card.class,
    portrait: card.portrait,
  })
}

function professionalCardIsCompatible(card: CharacterCard): boolean {
  return characterCardHasCompatibleProfessionalBlueprint(
    card,
    activeRule.value,
    activeRuleSummary.value?.ruleset_runtime?.id,
  )
}

function characterFromCard(card: CharacterCard): CreateCharacter {
  const legacy = legacyCharacterFromCard(card)
  if (!usesProfessionalBuilder.value || !professionalCardIsCompatible(card)) return legacy
  const canonical = cloneCharacter(card.ruleset_character as CharacterSheet)
  const binding = card.rule_binding || canonical.rule_binding
  return ensureCharacter({
    ...legacy,
    rule_binding: cloneCharacter(binding as CharacterSheet),
    ruleset_character: canonical,
  })
}

function cardNeedsReview(card: CharacterCard): boolean {
  return characterCardNeedsConversion(card, activeRule.value)
    || (usesProfessionalBuilder.value && !professionalCardIsCompatible(card))
}

watch([activeRule, gameLanguage], async ([id]) => {
  if (!id) { ruleDetail.value = null; return }
  try {
    const rd = await api<RuleDetailResponse>(`/rules/${id}?language=${encodeURIComponent(gameLanguage.value)}`)
    ruleDetail.value = rd.rule || null
  } catch { ruleDetail.value = null }
}, { immediate: true })
let adventureCatalogSequence = 0
watch([activeRule, world, gameLanguage, mode, loreChoice], async ([ruleId, worldId]) => {
  const sequence = ++adventureCatalogSequence
  if (!ruleId || !worldId || !showAdventurePackages.value) {
    adventures.value = []
    adventureId.value = ''
    playMode.value = 'free'
    return
  }
  try {
    const result = await api<AdventuresResponse>(
      `/adventures?rule_id=${encodeURIComponent(ruleId)}&world_id=${encodeURIComponent(worldId)}&language=${encodeURIComponent(gameLanguage.value)}`,
    )
    if (sequence !== adventureCatalogSequence) return
    adventures.value = result.adventures || []
    if (!adventures.value.some(item => item.adventure_id === adventureId.value && item.compatibility === 'compatible')) {
      adventureId.value = ''
      if (playMode.value === 'adventure') playMode.value = 'free'
    }
  } catch {
    if (sequence !== adventureCatalogSequence) return
    adventures.value = []
    adventureId.value = ''
    playMode.value = 'free'
  }
}, { immediate: true })
watch([aiPrompt, aiRule, aiAutoRule], () => { aiGeneratedRule.value = null })
watch(usesProfessionalBuilder, (enabled) => {
  showWizard.value = false
  showRulesetBuilder.value = false
  if (enabled) {
    if (characters.value.length === 1) {
      const current = characters.value[0]
      const empty = !current.background
        && !Object.keys(current.attributes || {}).length
        && !(current.skills || []).length
      if (empty) characters.value = []
    }
  } else if (!characters.value.length) {
    characters.value = [{ character_name: gameDefault(DEFAULT_ADVENTURER_ZH, 'Adventurer', DEFAULT_ADVENTURER_DE), background: '', identity: {}, attributes: {}, skills: [] }]
  }
})
watch(moduleId, async (value) => {
  moduleAdventureIds.value = []
  adventureId.value = ''
  if (!value) return
  try {
    const result = await moduleApi.detail(value)
    moduleAdventureIds.value = result.module?.adventures.map(item => item.adventure_id) || []
  } catch { /* the adventure selector remains empty until the module detail is available */ }
})
watch(sceneImageFile, (file) => {
  revokeSceneImageUrl(customSceneImageUrl.value)
  customSceneImageUrl.value = file ? URL.createObjectURL(file) : ''
})
let sceneResolveSequence = 0
watch([defaultSceneImageRef, activeRule], async ([reference, ruleId]) => {
  const sequence = ++sceneResolveSequence
  const previous = defaultSceneImageUrl.value
  const resolved = await resolveSceneImageUrl(reference, ruleId).catch(() => ruleSceneUrl(ruleId))
  if (sequence !== sceneResolveSequence) {
    revokeSceneImageUrl(resolved)
    return
  }
  defaultSceneImageUrl.value = resolved
  if (previous !== resolved) revokeSceneImageUrl(previous)
}, { immediate: true })
watch(seed, (value) => { if (value.trim()) sceneImageFile.value = null })
watch(solo, (value) => {
  if (!narrativePerspectiveTouched.value) {
    narrativePerspective.value = value ? 'immersive' : 'third_person'
  }
})
watch(locale, (next) => { gameLanguage.value = next })
watch(gameLanguage, async (language, previous) => {
  if (language === previous) return
  try {
    const [w, r] = await Promise.all([
      api<WorldTemplatesResponse>(`/world-templates?language=${encodeURIComponent(language)}`),
      api<RulesResponse>(`/rules?language=${encodeURIComponent(language)}`),
    ])
    worlds.value = w.templates || []
    rules.value = r.rules || []
  } catch { /* keep the last usable catalog on a transient request failure */ }
})
watch([gameLanguage, worlds], () => {
  if (world.value && availableWorlds.value.some(w => worldIdOf(w) === world.value)) return
  world.value = worldIdOf(availableWorlds.value[0] || worlds.value[0] || {})
})
watch(world, (worldId) => {
  if (mode.value !== 'template' || !worldId) return
  const defaultRule = String(worlds.value.find(item => worldIdOf(item) === worldId)?.default_rule || '')
  if (defaultRule && rules.value.some(item => item.rule_id === defaultRule)) rule.value = defaultRule
})
watch([gameLanguage, loreWorlds], () => {
  if (!loreChoice.value.startsWith('copy:')) return
  const selected = loreChoice.value.slice('copy:'.length)
  if (!availableLoreWorlds.value.some(w => worldIdOf(w) === selected)) loreChoice.value = '__builtin__'
})

onMounted(async () => {
  const settingsPromise = settings.load().finally(() => { settingsChecked.value = true })
  const [w, r, lw, cs] = await Promise.all([
    api<WorldTemplatesResponse>(`/world-templates?language=${encodeURIComponent(gameLanguage.value)}`),
    api<RulesResponse>(`/rules?language=${encodeURIComponent(gameLanguage.value)}`),
    api<WorldListResponse>('/worlds'),
    api<CharacterCardsResponse>('/character-cards'),
    settingsPromise,
  ])
  worlds.value = w.templates || []
  rules.value = r.rules || []
  loreWorlds.value = lw.worlds || []
  cards.value = cs.cards || []
  world.value = worldIdOf(availableWorlds.value[0] || worlds.value[0] || {})
  // 从世界画廊跳入时预填所选世界（仅当它仍在当前语言可用列表中）。
  const preselectedWorld = String(route.query.world || '')
  if (preselectedWorld && availableWorlds.value.some(item => worldIdOf(item) === preselectedWorld)) {
    world.value = preselectedWorld
  }
  const worldDefaultRule = String(activeWorldTemplate.value?.default_rule || '')
  rule.value = rules.value.some(item => item.rule_id === worldDefaultRule)
    ? worldDefaultRule
    : (rules.value[0]?.rule_id || '')
  aiRule.value = rule.value
  void moduleApi.list().then(result => { modules.value = result.modules || [] }).catch(() => {})
  characters.value = usesProfessionalBuilder.value
    ? []
    : [{ character_name: gameDefault(DEFAULT_ADVENTURER_ZH, 'Adventurer', DEFAULT_ADVENTURER_DE), background: '', identity: {}, attributes: {}, skills: [] }]
})

onBeforeUnmount(() => {
  revokeSceneImageUrl(defaultSceneImageUrl.value)
  revokeSceneImageUrl(customSceneImageUrl.value)
})

function openWizard(idx: number | null) {
  editIdx.value = idx
  if (usesProfessionalBuilder.value) {
    const canonical = idx === null ? undefined : characters.value[idx]?.ruleset_character
    const build = canonical && typeof canonical === 'object' && !Array.isArray(canonical)
      ? ((canonical as Record<string, unknown>).build as Record<string, unknown> | undefined)
      : undefined
    const levels = Array.isArray(build?.class_levels) ? build.class_levels : []
    const level = Number((levels[0] as Record<string, unknown> | undefined)?.level || build?.level || 1)
    if (idx !== null && level > 1) {
      editIdx.value = null
      toast.info(gameDefault(
        '这个角色已经高于 1 级，已可直接用于本局，不需要重新建卡。高级建卡器只负责创建 1 级角色；职业能力请在角色管理或对局内通过“职业升级”调整。',
        'This character is already above level 1 and can be used as-is. The advanced builder creates level-1 characters; use Class advancement for later levels.',
        'Dieser Charakter ist bereits über Stufe 1 und kann direkt für dieses Spiel verwendet werden, ein erneutes Erstellen ist nicht nötig. Der erweiterte Charaktereditor erstellt nur Charaktere auf Stufe 1; Klassenfähigkeiten bitte in der Charakterverwaltung oder im Spiel über „Klassenaufstieg” anpassen.',
      ))
      return
    }
    showRulesetBuilder.value = true
  }
  else showWizard.value = true
}
function onWizardSubmit(c: CharacterSheet) {
  const character = ensureCharacter(c)
  if (editIdx.value !== null) characters.value[editIdx.value] = character
  else characters.value.push(character)
  showWizard.value = false
  editIdx.value = null
}
function onRulesetSubmit(c: CharacterSheet) {
  const character = ensureCharacter(c)
  if (editIdx.value !== null) characters.value[editIdx.value] = character
  else characters.value.push(character)
  showRulesetBuilder.value = false
  editIdx.value = null
}
function onPickerPick(c: CharacterCard) {
  const character = characterFromCard(c)
  characters.value.push(character)
  showPicker.value = false
  if (cardNeedsReview(c)) {
    editIdx.value = characters.value.length - 1
    if (usesProfessionalBuilder.value) showRulesetBuilder.value = true
    else showWizard.value = true
    toast.info(t('cardRuleConversionReview'))
  } else {
    toast.success(t('addedFromLibrary'))
  }
}
async function importCardFile(file: File) {
  const r = await importTavernCard(file, { target: 'character_card' })
  const card = r.card
  if (!card) throw new Error(t('importFailed'))
  cards.value.push(card)
  const character = characterFromCard(card)
  characters.value.push(character)
  if (cardNeedsReview(card)) {
    editIdx.value = characters.value.length - 1
    if (usesProfessionalBuilder.value) showRulesetBuilder.value = true
    else showWizard.value = true
    toast.info(t('cardRuleConversionReview'))
  } else {
    toast.success(t('importedCharacter', { name: card.character_name }))
  }
}
function onStImport(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importCardFile(file).catch(err => toast.error(errorMessage(err)))
  input.value = ''
}
function onImportDfCard(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importCardFile(file).catch(err => toast.error(errorMessage(err)))
  input.value = ''
}
function removeCharacter(idx: number) {
  if (characters.value.length <= 1) { toast.error(t('atLeastOneCharacter')); return }
  characters.value.splice(idx, 1)
  // 控制方式与角色按 index 平行保存，必须一起删；否则后面的角色会继承被删角色的
  // control（删掉 A(human) 之后 B 会变成 human，而它原本是 ai）。
  cardControl.value = removeCardControl(cardControl.value, idx)
}

function canNext() {
  if (step.value === 1) {
    if (seed.value.trim()) return true
    if (!activeRule.value) return false
    if (showAdventurePackages.value && playMode.value === 'adventure' && !adventureId.value) return false
    if (mode.value === 'ai' && !aiPrompt.value.trim()) return false
    if (mode.value === 'custom' && !customName.value.trim()) return false
    return true
  }
  if (step.value === 3) return characters.value.length >= 1 && characters.value.every(c => c.character_name?.trim())
  return true
}
function requireApiConfiguration() {
  if (!showApiSetupHint.value) return
  throw new Error(t('apiSetupRequired'))
}
async function prepareAiRule() {
  if (mode.value !== 'ai' || !aiAutoRule.value || aiGeneratedRule.value?.rule_id) return
  requireApiConfiguration()
  if (!aiPrompt.value.trim()) throw new Error(t('enterWorldPrompt'))
  toast.info(t('generatingRule'))
  const r = await api<GeneratedRuleResponse>('/generate-rule', {
    method: 'POST',
    body: JSON.stringify({ prompt: aiPrompt.value, source_rule_id: aiRule.value, language: gameLanguage.value }),
  })
  if (!r.ok && r.error) throw new Error(r.error)
  if (!r.rule_id) throw new Error(t('missingRuleId'))
  aiGeneratedRule.value = r
  const all = await api<RulesResponse>('/rules').catch(() => null)
  if (all?.rules) rules.value = all.rules
  toast.success(`${t('generatedRuleToast')}${r.rule_name || r.rule_id}`)
}
async function nextStep() {
  if (!canNext() || step.value >= 4) return
  busy.value = true; error.value = ''
  try {
    if (step.value === 1) await prepareAiRule()
    step.value = (step.value + 1) as Step
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
function prevStep() { if (step.value > 1) step.value = (step.value - 1) as Step }

async function create() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    requireApiConfiguration()
    const players = characters.value.map((c, i) => {
      const card = cloneCharacter(c)
      // 每张卡在「角色」步骤都有明确的控制方式，创建 payload 直接带上它；
      // 服务端把它写成 players[uid].control.mode（human / ai / unclaimed），
      // 前端不自己造 AI 状态，也不修改 control revision。
      return { ...card, control: cardControlAt(cardControl.value, i) }
    })
    const selectedSceneImage = sceneImageFile.value ? await uploadSceneImage(sceneImageFile.value) : undefined
    if (seed.value.trim()) {
      const r = await api<GameMutationResponse>('/games/create-from-seed', { method: 'POST', body: JSON.stringify({ seed_code: seed.value.trim(), solo: solo.value, players, language: gameLanguage.value, scene_image: selectedSceneImage, narrative_perspective: narrativePerspective.value }) })
      if (!r.ok && r.error) throw new Error(r.error)
      if (!r.game_key) throw new Error(t('missingGameId'))
      rememberCurrentGame(r.game_key, r.world_name || '')
      await router.push({ name: 'play', query: { game: r.game_key } }); return
    }
    const selectedMapBackground = mapBackgroundFile.value
      ? await uploadMapBackground(mapBackgroundFile.value)
      : mapBackgroundSelection(mapBackgroundChoice.value)
    const payload: Record<string, unknown> = { solo: solo.value, difficulty: difficulty.value, rule_id: activeRule.value, play_mode: showAdventurePackages.value ? playMode.value : 'free', adventure_id: showAdventurePackages.value && playMode.value === 'adventure' ? adventureId.value : '', description: description.value, room_password: openRoom.value ? '' : (roomPassword.value.trim() || null), players, language: gameLanguage.value, scene_image: selectedSceneImage, map_background: selectedMapBackground, narrative_perspective: narrativePerspective.value, gm_style_override: gmStyleFollowWorld.value ? null : { ...gmStyle.value }, advancement_mode: supportsAdvancementPolicy.value ? advancementMode.value : 'milestone', advancement_authority: supportsAdvancementPolicy.value ? advancementAuthority.value : 'ai_gm' }
    let worldId = ''
    if (mode.value === 'template') {
      worldId = world.value; payload.world_id = worldId
      payload.game_name = name.value || worldNameOf(worlds.value.find(w => worldIdOf(w) === world.value) || {}) || gameDefault(DEFAULT_NEW_ADVENTURE_ZH, 'New Adventure', DEFAULT_NEW_ADVENTURE_DE)
    } else if (mode.value === 'custom') {
      worldId = 'custom_' + Date.now(); payload.world_id = worldId
      payload.world_name = customName.value.trim() || gameDefault(DEFAULT_MY_ADVENTURE_ZH, 'My Adventure', DEFAULT_MY_ADVENTURE_DE); payload.custom_world = true; payload.description = customDesc.value
    } else if (mode.value === 'ai') {
      if (!aiPrompt.value.trim()) throw new Error(t('enterWorldPrompt'))
      if (aiAutoRule.value && !aiGeneratedRule.value?.rule_id) await prepareAiRule()
      const selectedRule = activeRule.value
      payload.rule_id = selectedRule
      const gw = await api<GeneratedWorldResponse>('/generate-world', { method: 'POST', body: JSON.stringify({ prompt: aiPrompt.value, rule_id: selectedRule, language: gameLanguage.value }) })
      if (!gw.ok && gw.error) throw new Error(gw.error)
      worldId = gw.world_id; payload.world_id = worldId; payload.game_name = gw.world_name || gameDefault(DEFAULT_AI_WORLD_ZH, 'AI Generated World', DEFAULT_AI_WORLD_DE)
    }
    if (loreChoice.value === '__builtin__') payload.create_lorebook = false
    else if (loreChoice.value === '__blank__') {
      payload.source_world_id = worldId; payload.world_id = worldId + '_blank_' + Date.now()
      payload.game_name = String(payload.game_name || '') + gameDefault(BLANK_LOREBOOK_SUFFIX_ZH, ' (Blank Lorebook)', BLANK_LOREBOOK_SUFFIX_DE); payload.create_lorebook = true; payload.blank_lorebook = true
    } else if (loreChoice.value.startsWith('copy:')) {
      const src = loreChoice.value.slice(5)
      payload.source_world_id = worldId; payload.world_id = worldId + '_copy_' + Date.now()
      payload.game_name = String(payload.game_name || '') + gameDefault(COPIED_LOREBOOK_SUFFIX_ZH, ' (Copied Lorebook)', COPIED_LOREBOOK_SUFFIX_DE); payload.create_lorebook = true; payload.lorebook_world_id = src
    }
    const r = await api<GameMutationResponse>('/games/create', { method: 'POST', body: JSON.stringify(payload) })
    if (!r.ok && r.error) throw new Error(r.error)
    if (!r.game_key) throw new Error(t('missingGameId'))
    if (r.generated_password) window.alert(t('roomPasswordGenerated', { pwd: r.generated_password }))
    rememberCurrentGame(r.game_key, r.world_name || String(payload.game_name || ''))
    await router.push({ name: 'play', query: { game: r.game_key } })
  } catch (e: unknown) {
    error.value = errorMessage(e)
    toast.error(error.value)
  } finally { busy.value = false }
}
</script>

<template>
  <section class="view create-page reference-create-page" :style="sceneImageStyle(selectedSceneImageUrl)">
    <header class="create-page-header">
      <button class="create-back" @click="router.push({ name: 'overview' })">←</button>
      <div><span class="section-kicker">NEW CAMPAIGN</span><h1>{{ t('createTitle') }}</h1><p>{{ t('createSubtitle') }}</p></div>
    </header>

    <div class="create-wizard-shell">
      <aside class="create-journey-rail">
        <span class="create-rail-sigil">✦</span>
        <nav class="create-step-nav">
          <div v-for="n in 4" :key="n" :class="['create-step-nav-item', { active: step === n, done: step > n }]">
            <span>{{ step > n ? '✓' : n }}</span>
            <div><strong>{{ stepTitle(n) }}</strong><small>STEP 0{{ n }}</small></div>
          </div>
        </nav>
        <div class="create-rail-summary">
          <span>{{ t('gameMode') }}</span><strong>{{ solo ? t('solo') : t('multiplayer') }}</strong>
          <span>{{ t('rule') }}</span><strong>{{ activeRule || '—' }}</strong>
          <span>{{ t('charactersCount') }}</span><strong>{{ characters.length }}</strong>
        </div>
      </aside>

      <main class="create-stage-panel">
        <div v-if="showApiSetupHint" class="notice create-api-hint">
          <div><strong>{{ t('apiSetupRequiredTitle') }}</strong><p>{{ t('apiSetupRequiredHint') }}</p></div>
          <button type="button" class="primary" @click="router.push({ name: 'settings' })">{{ t('goToApiSettings') }}</button>
        </div>

        <header class="create-stage-head">
          <span>0{{ step }}</span>
          <div><h2>{{ stepTitle(step) }}</h2><p>{{ t('createSubtitle') }}</p></div>
        </header>

        <section v-if="step === 1" class="create-step-card create-content-stage">
          <div class="create-field-grid create-field-grid-compact">
            <label><span>{{ t('gameLanguage') }}</span><select v-model="gameLanguage"><option value="zh-CN">{{ t('chinese') }}</option><option value="en">{{ t('english') }}</option><option value="de">{{ t('german') }}</option></select><small>{{ t('gameLanguageHint') }}</small></label>
            <label><span>{{ t('seedCode') }}</span><input v-model="seed" :placeholder="t('seedPlaceholder')"><small>{{ t('restoreBySeed') }}</small></label>
          </div>
          <template v-if="!seed">
            <div class="create-mode-cards">
              <button type="button" :class="{ active: mode === 'template' }" @click="mode = 'template'"><b>◇</b><strong>{{ t('modeTemplate') }}</strong></button>
              <button type="button" :class="{ active: mode === 'custom' }" @click="mode = 'custom'"><b>✎</b><strong>{{ t('modeCustom') }}</strong></button>
              <button type="button" :class="{ active: mode === 'ai' }" @click="mode = 'ai'"><b>✦</b><strong>{{ t('modeAi') }}</strong></button>
            </div>
            <div class="create-config-surface">
              <template v-if="mode === 'template'">
                <label><span>{{ t('worldTemplate') }}</span><select v-model="world"><option v-for="w in availableWorlds" :key="worldIdOf(w)" :value="worldIdOf(w)">{{ worldOptionLabel(w) }}</option></select></label>
                <label><span>{{ t('adventureName') }}</span><input v-model="name" :placeholder="t('useWorldName')"></label>
                <div v-if="recommendedRulesList.length" class="create-recommended-rules wide">
                  <span class="rec-head">{{ t('recommendedRules') }}</span>
                  <div class="rec-grid">
                    <button v-for="r in recommendedRulesList" :key="r.rule_id" type="button" :class="['rec-card', { active: rule === r.rule_id }]" @click="rule = r.rule_id">
                      <strong>{{ ruleNameOf(r) }}</strong>
                      <span class="recommendation-badges"><small :class="{ professional: isProfessionalRule(r) }">{{ recommendationBadge(r) }}</small><small v-if="isDndAdvancedRule(r)" class="beta-badge">{{ gameDefault('测试版', 'Beta', 'Beta') }}</small></span>
                      <p>{{ ruleDescriptionOf(r) }}</p>
                    </button>
                  </div>
                  <small class="rec-hint">{{ t('recommendedHint') }}</small>
                </div>
              </template>
              <template v-else-if="mode === 'custom'">
                <label><span>{{ t('customWorldName') }}</span><input v-model="customName" :placeholder="t('customWorldPlaceholder')"></label>
                <label class="wide"><span>{{ t('worldDescription') }}</span><textarea v-model="customDesc" rows="5" :placeholder="t('worldDescriptionPlaceholder')"></textarea></label>
              </template>
              <template v-else>
                <label class="wide"><span>{{ t('aiWorldDescription') }}</span><textarea v-model="aiPrompt" rows="6" :placeholder="t('aiWorldPlaceholder')"></textarea></label>
                <label><span>{{ t('baseRule') }}</span><select v-model="aiRule"><option v-for="r in rules" :key="r.rule_id" :value="r.rule_id">{{ ruleNameOf(r) }}</option></select></label>
                <label class="create-check"><input type="checkbox" v-model="aiAutoRule"> {{ t('aiRuleDraft') }}</label>
                <p v-if="aiGeneratedRule?.rule_id" class="notice wide">{{ t('generatedRule') }}{{ aiGeneratedRule.rule_name || aiGeneratedRule.rule_id }}{{ t('generatedRuleHint') }}</p>
              </template>
              <label v-if="mode !== 'ai'"><span>{{ t('rule') }}</span><select v-model="rule"><option v-for="r in rules" :key="r.rule_id" :value="r.rule_id">{{ ruleNameOf(r) }}</option></select></label>
              <label><span>{{ t('lorebookSource') }}</span><select v-model="loreChoice"><option value="__builtin__">{{ t('builtinLorebook') }}</option><option value="__blank__">{{ t('blankLorebook') }}</option><option v-for="w in availableLoreWorlds" :key="worldIdOf(w)" :value="'copy:' + worldIdOf(w)">{{ t('copyFrom') }}{{ worldNameOf(w) }} · {{ worldLanguageLabel(w) }}</option></select></label>
              <div v-if="showAdventurePackages" class="wide create-adventure-package-field">
                <label>
                  <span>{{ gameDefault('玩法模式', 'Play mode', 'Spielmodus') }}</span>
                  <div class="narrative-perspective-cards">
                    <button type="button" :class="{ active: playMode === 'free' }" @click="selectPlayMode('free')">
                      <strong>{{ gameDefault('标准自由对局', 'Standard free play', 'Standard-Freispiel') }}</strong>
                      <small>{{ gameDefault('由 GM 使用通用遭遇或 AI 临时遭遇，自由推进剧情。', 'The GM freely advances the story using catalogued or AI temporary encounters.', 'Der GM treibt die Geschichte frei voran, mit katalogisierten oder KI-generierten Begegnungen.') }}</small>
                    </button>
                    <button type="button" :class="{ active: playMode === 'adventure' }" @click="selectPlayMode('adventure')">
                      <strong>{{ gameDefault('冒险包剧情', 'Adventure story', 'Abenteuerpaket-Handlung') }}</strong>
                      <small>{{ gameDefault('遵循冒险节点，并使用节点绑定的剧情遭遇。', 'Follow adventure nodes and their bound story encounters.', 'Folgt Abenteuerknoten und deren gebundenen Story-Begegnungen.') }}</small>
                    </button>
                  </div>
                  <div v-if="playMode === 'adventure'" class="create-adventure-select-row">
                    <select v-if="modules.length" v-model="moduleId">
                      <option value="">{{ t('navModules') }}</option>
                      <option v-for="item in modules" :key="item.id" :value="item.id">{{ item.name }}</option>
                    </select>
                    <select v-model="adventureId">
                      <option value="" disabled>{{ gameDefault('请选择冒险包', 'Choose an adventure package', 'Abenteuerpaket wählen') }}</option>
                      <option v-for="item in availableModuleAdventures" :key="item.adventure_id" :value="item.adventure_id" :disabled="item.compatibility !== 'compatible'">
                        {{ item.name }} · {{ item.estimated_minutes }} {{ gameDefault('分钟', 'min', 'Min.') }}{{ item.compatibility !== 'compatible' ? gameDefault('（需匹配推荐世界）', ' (requires its recommended world)', ' (benötigt die empfohlene Welt)') : '' }}
                      </option>
                    </select>
                    <a class="create-manage-adventures" :href="adventureManagerHref" target="_blank" rel="noopener">{{ t('manageAdventurePackages') }}</a>
                  </div>
                  <small>{{ selectedAdventure?.summary || gameDefault('不选择冒险包时，世界书照常生效，进入正常高级规则对局。冒险包只提供剧情节点，不会替换你选择的世界书。', 'Without an adventure package, the selected world book remains active in standard advanced play. An adventure package adds story nodes without replacing that world book.', 'Ohne Abenteuerpaket bleibt das gewählte Weltenbuch wie gewohnt aktiv, im normalen erweiterten Regelspiel. Ein Abenteuerpaket fügt nur Handlungsknoten hinzu, ohne dein gewähltes Weltenbuch zu ersetzen.') }}</small>
                  <small v-if="selectedAdventure?.recommended_world_id" class="adventure-recommendation">{{ gameDefault('推荐世界仅供参考：', 'Recommended world: ', 'Empfohlene Welt: ') }}{{ selectedAdventure.recommended_world_id }}{{ gameDefault('；复制或自定义世界书也可以使用。', '; copied or custom world books are also supported.', '; kopierte oder eigene Weltenbücher funktionieren ebenfalls.') }}</small>
                </label>
              </div>
              <label class="wide"><span>{{ t('extraBackground') }}</span><textarea v-model="description" rows="4" :placeholder="t('extraBackgroundPlaceholder')"></textarea></label>
            </div>
          </template>
        </section>

        <section v-else-if="step === 2" class="create-step-card create-game-settings-stage">
          <div class="create-config-surface">
            <label :class="{ wide: seed.trim() }"><span>{{ t('gameMode') }}</span><select v-model.number="solo"><option :value="true">{{ t('solo') }}</option><option :value="false">{{ t('multiplayer') }}</option></select></label>
            <template v-if="!seed.trim()">
              <label><span>{{ t('difficulty') }}</span><select v-model="difficulty"><option :value="DIFFICULTY_EASY">{{ t('easy') }}</option><option :value="DIFFICULTY_NORMAL">{{ t('normal') }}</option><option :value="DIFFICULTY_HARDCORE">{{ t('hardcore') }}</option></select></label>
            </template>
            <fieldset class="wide narrative-perspective-field">
              <legend>{{ t('narrativePerspective') }}</legend>
              <div class="narrative-perspective-cards">
                <button type="button" :class="{ active: narrativePerspective === 'immersive' }" @click="selectNarrativePerspective('immersive')">
                  <strong>{{ t('narrativeImmersive') }}</strong>
                  <small>{{ t('narrativeImmersiveHint') }}</small>
                </button>
                <button type="button" :class="{ active: narrativePerspective === 'third_person' }" @click="selectNarrativePerspective('third_person')">
                  <strong>{{ t('narrativeThirdPerson') }}</strong>
                  <small>{{ t('narrativeThirdPersonHint') }}</small>
                </button>
              </div>
            </fieldset>
            <fieldset class="wide narrative-perspective-field create-gm-style-field">
              <legend>{{ t('gmStyleTitle') }}</legend>
              <div class="narrative-perspective-cards">
                <button type="button" :class="{ active: gmStyleFollowWorld }" @click="gmStyleFollowWorld = true">
                  <strong>{{ t('gmStyleFollowWorld') }}</strong>
                  <small>{{ gameDefault('使用当前世界保存的默认文风。', 'Use the narration style saved with this world.', 'Verwendet den mit dieser Welt gespeicherten Erzählstil.') }}</small>
                </button>
                <button type="button" :class="{ active: !gmStyleFollowWorld }" @click="gmStyleFollowWorld = false">
                  <strong>{{ t('gmStyleOverrideCurrent') }}</strong>
                  <small>{{ gameDefault('只覆盖本局，从下一次 AI GM 回复起生效。', 'Override only this game, starting with the next AI GM response.', 'Überschreibt nur dieses Spiel, gültig ab der nächsten Antwort des KI-Spielleiters.') }}</small>
                </button>
              </div>
              <small>{{ t('gmStyleHint') }}</small>
              <div v-if="!gmStyleFollowWorld" class="create-config-surface">
                <label>
                  <span>{{ t('gmStyleTone') }}</span>
                  <select v-model="gmStyle.tone">
                    <option value="">{{ t('gmStyleToneDefault') }}</option>
                    <option value="literary">{{ t('gmStyleToneLiterary') }}</option>
                    <option value="direct">{{ t('gmStyleToneDirect') }}</option>
                    <option value="humorous">{{ t('gmStyleToneHumorous') }}</option>
                    <option value="dark">{{ t('gmStyleToneDark') }}</option>
                  </select>
                </label>
                <label>
                  <span>{{ t('gmStyleVerbosity') }}</span>
                  <select v-model="gmStyle.verbosity">
                    <option value="brief">{{ t('gmStyleVerbosityBrief') }}</option>
                    <option value="normal">{{ t('gmStyleVerbosityNormal') }}</option>
                    <option value="detailed">{{ t('gmStyleVerbosityDetailed') }}</option>
                  </select>
                </label>
                <label>
                  <span>{{ t('gmStylePace') }}</span>
                  <select v-model="gmStyle.pace">
                    <option value="slow">{{ t('gmStylePaceSlow') }}</option>
                    <option value="normal">{{ t('gmStylePaceNormal') }}</option>
                    <option value="fast">{{ t('gmStylePaceFast') }}</option>
                  </select>
                </label>
                <label class="wide">
                  <span>{{ t('gmStyleCustom') }}</span>
                  <textarea v-model="gmStyle.custom_instructions" rows="3" maxlength="2000" :placeholder="t('gmStyleCustomPlaceholder')" />
                </label>
              </div>
            </fieldset>
            <template v-if="!seed.trim()">
              <div v-if="supportsAdvancementPolicy" class="wide advancement-policy-field">
                <div class="advancement-policy-grid">
                  <label>
                    <span>{{ t('advancementMode') }}</span>
                    <select v-model="advancementMode">
                      <option value="milestone">{{ t('advancementMilestone') }}</option>
                      <option value="xp">{{ t('advancementXp') }}</option>
                    </select>
                    <small>{{ advancementMode === 'milestone' ? t('advancementMilestoneHint') : t('advancementXpHint') }}</small>
                  </label>
                  <label>
                    <span>{{ t('advancementAuthority') }}</span>
                    <select v-model="advancementAuthority">
                      <option value="ai_gm">{{ t('advancementAiGm') }}</option>
                      <option value="gm">{{ t('advancementHumanGm') }}</option>
                    </select>
                    <small>{{ t('advancementEntitlementHint') }}</small>
                  </label>
                </div>
              </div>
              <label class="wide"><span>{{ t('roomPassword') }}</span><input v-model="roomPassword" :placeholder="t('roomPasswordPlaceholder')"></label>
              <label class="wide create-check"><input type="checkbox" v-model="openRoom"><span>{{ t('roomOpen') }}</span></label>
            </template>
            <AdventureSceneImagePicker v-model="sceneImageFile" class="wide" :default-url="defaultSceneImageUrl" />
            <details v-if="!seed.trim()" class="wide create-advanced-settings">
              <summary>{{ t('mapBackgroundAdvanced') }}</summary>
              <MapBackgroundPicker v-model="mapBackgroundChoice" v-model:file="mapBackgroundFile" />
            </details>
          </div>
        </section>

        <section v-else-if="step === 3" class="create-step-card create-character-stage">
          <div class="create-character-actions">
            <button class="primary" @click="openWizard(null)">＋ {{ t('newCharacter') }}</button><button @click="showPicker = true">{{ t('pickFromLibrary') }}</button><button @click="dfInput?.click()">{{ t('importDiceframeCard') }}</button><button @click="fileInput?.click()">{{ t('importStCard') }}</button>
            <input ref="dfInput" type="file" accept=".json,application/json" hidden @change="onImportDfCard"><input ref="fileInput" type="file" accept=".png,.json" hidden @change="onStImport">
          </div>
          <div class="create-character-grid">
            <article v-for="(c, i) in characters" :key="i" class="create-character-card">
              <PortraitImage :portrait="c.portrait" :rule-id="activeRule" :seed="c.character_name || String(i)" :name="c.character_name" :size="72" />
              <div>
                <h3>{{ c.character_name || t('unnamed') }}</h3>
                <p>{{ c.identity?.origin || c.race || '' }} · {{ c.identity?.archetype || c.class || '' }}</p>
                <small>{{ c.skills?.length || 0 }} {{ t('skills') }}</small>
              </div>
              <div class="actions">
                <!-- 控制方式是一个按钮而不是下拉：按钮显示当前状态，点一下切到下一个
                     （玩家 → AI 托管 → 等待认领 → 玩家），与「编辑 / 删除」同一排。 -->
                <button
                  type="button"
                  class="create-character-control-button"
                  :title="controlSwitchHint(i)"
                  :aria-label="controlSwitchHint(i)"
                  @click="cycleControl(i)"
                >{{ controlLabel(controlOf(i)) }}</button>
                <button @click="openWizard(i)">{{ t('edit') }}</button>
                <button class="danger" @click="removeCharacter(i)">{{ t('remove') }}</button>
              </div>
            </article>
            <button class="create-character-empty" @click="openWizard(null)"><b>＋</b><span>{{ t('newCharacter') }}</span></button>
          </div>
        </section>

        <section v-else class="create-step-card create-confirm-stage">
          <div class="create-confirm-cover" :style="sceneImageStyle(selectedSceneImageUrl)"><span>✦</span><h2>{{ t('confirmCreate') }}</h2><p>{{ confirmationName }}</p></div>
          <div class="create-confirm-grid">
            <article><span>{{ t('world') }}</span><strong>{{ confirmationWorld }}</strong></article>
            <article><span>{{ t('rule') }}</span><strong>{{ ruleNameOf(rules.find(r => r.rule_id === activeRule) || { rule_id: activeRule }) }}</strong></article>
            <article v-if="supportsAdventurePackages"><span>{{ gameDefault('冒险模式', 'Adventure mode', 'Abenteuermodus') }}</span><strong>{{ selectedAdventure?.name || gameDefault('标准自由对局', 'Standard free play', 'Standard-Freispiel') }}</strong></article>
            <article><span>{{ t('narrativePerspective') }}</span><strong>{{ narrativePerspective === 'immersive' ? t('narrativeImmersive') : t('narrativeThirdPerson') }}</strong></article>
            <article v-if="supportsAdvancementPolicy && !seed.trim()"><span>{{ t('advancementMode') }}</span><strong>{{ advancementMode === 'milestone' ? t('advancementMilestone') : t('advancementXp') }} · {{ advancementAuthority === 'ai_gm' ? t('advancementAiGm') : t('advancementHumanGm') }}</strong></article>
            <article><span>{{ t('difficulty') }}</span><strong>{{ difficulty === DIFFICULTY_EASY ? t('easy') : difficulty === DIFFICULTY_HARDCORE ? t('hardcore') : t('normal') }}</strong></article>
            <article><span>{{ t('charactersCount') }}</span><strong>{{ characters.length }}</strong></article>
          </div>
          <div class="create-confirm-characters"><span v-for="(c, i) in characters" :key="i">{{ c.character_name }}</span></div>
          <!-- 确认页不再逐张列出「名字 + 控制方式」：名字上面已经有胶囊，控制方式在
               「角色」步骤的按钮上就是当前状态，重复一遍没有信息量。 -->
        </section>

        <p v-if="error" class="error-banner">{{ error }}</p>
        <footer class="create-actions"><button @click="router.push({ name: 'overview' })">{{ t('cancel') }}</button><button v-if="step > 1" @click="prevStep">{{ t('previous') }}</button><button v-if="step < 4" class="primary" :disabled="busy || !canNext()" @click="nextStep">{{ busy && step === 1 ? t('preparing') : t('next') }} →</button><button v-else class="primary" :disabled="busy" @click="create">{{ busy ? t('creating') : t('createAndEnter') }} →</button></footer>
      </main>
    </div>

    <CharacterWizard v-if="showWizard" :rule-meta="ruleDetail" :rule-attrs="ruleAttrs" :attr-total="attrTotal" :skill-pool="skillPool" :rule-id="activeRule" :language="gameLanguage" :initial="editIdx !== null ? characters[editIdx] : undefined" @submit="onWizardSubmit" @cancel="showWizard = false" />
    <RulesetExperienceHost v-if="showRulesetBuilder" :rule-id="activeRule" :language="gameLanguage" :initial="editIdx !== null ? characters[editIdx] : undefined" @submit="onRulesetSubmit" @cancel="showRulesetBuilder = false" />
    <CharacterCardPicker v-if="showPicker" :cards="cards" :target-rule-id="activeRule" @pick="onPickerPick" @close="showPicker = false" />
  </section>
</template>
