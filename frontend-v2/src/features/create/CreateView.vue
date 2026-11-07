<script setup lang="ts">
import { adventureMatchesRule, adventureRequirementLabel } from '@/features/adventures/compatibility'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { defaultGamePreferences } from '@/features/game-preferences/model'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import { accessTokenStorageKey } from '@/api/connection'
import type { CharacterCard, CharacterCardsResponse, CharacterSheet, GameMutationResponse, GeneratedRuleResponse, GeneratedWorldResponse, GmStyle, RuleDetailResponse, RuleSummary, RuleTemplate, RulesResponse, SceneImageRef, StoryMode, WorldListResponse, WorldSummary, WorldTemplateSummary, WorldTemplatesResponse } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useLocale, type Locale } from '@/composables/useLocale'
import { i18n, SUPPORTED_LOCALES, localeChain, normalizeLocale } from '@/i18n'
import CharacterWizard from '@/components/admin/CharacterWizard.vue'
import AdventureLaunchSummary from '@/features/library/AdventureLaunchSummary.vue'
import WorldLorebookSummary from '@/features/create/WorldLorebookSummary.vue'
import LorebookSourcePicker from '@/features/create/LorebookSourcePicker.vue'
import type { CreationLorebook } from '@/features/create/creationLorebooks'
import CharacterCardPicker from '@/components/admin/CharacterCardPicker.vue'
import PortraitImage from '@/components/PortraitImage.vue'
import AdventureSceneImagePicker from '@/components/common/AdventureSceneImagePicker.vue'
import MapBackgroundPicker from '@/components/common/MapBackgroundPicker.vue'
import RulesetExperienceHost from '@/features/rulesets/RulesetExperienceHost.vue'
import { importTavernCard } from '@/utils/characterImport'
import { rememberCurrentGame } from '@/stores/gameContext'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { filterByContentLanguage, localeLabel } from '@/utils/contentLanguage'
import { recommendedRuleSummaries } from '@/utils/recommendedRules'
import {
  characterCardHasCompatibleProfessionalBlueprint,
  characterCardNeedsConversion,
} from '@/utils/characterCards'
import { ruleSceneUrl } from '@/composables/useBackgroundImages'
import { pickSceneImageReference, resolveSceneImageUrl, revokeSceneImageUrl, sceneImageCandidates, sceneImageStyle, uploadSceneImage } from '@/api/sceneImages'
import { mapBackgroundSelection, uploadMapBackground } from '@/api/mapBackgrounds'
import { isLlmConfigReady } from '@/utils/modelConfiguration'
import { roomPasswordProblem, roomPasswordProblemMessage } from '@/utils/roomPassword'
import { moduleApi, type ModuleSummary } from '@/api/modules'
import { adventureKey, useAdventureSelection } from '@/features/create/adventureSelection'
import { applyLorebookSource, applyStoryMode, type LorebookSource } from '@/features/create/createPayload'
import { normalizeStoryMode } from '@/features/create/storyMode'
import { canAdvanceCreateStep, createStepProgress } from '@/features/create/createFlow'
import { clearCreateDraft, loadCreateDraft, saveCreateDraft, setCreateDraftSession, type CreateDraftSnapshot } from '@/features/create/createDraft'
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
type AdvancementMode = 'milestone' | 'xp'
type AdvancementAuthority = 'ai_gm' | 'gm'
const DIFFICULTY_EASY = '\u8f7b\u677e'
const DIFFICULTY_NORMAL = '\u6807\u51c6'
const DIFFICULTY_HARDCORE = '\u786c\u6838'
const DEFAULT_ADVENTURER_ZH = '\u5192\u9669\u8005'
const DEFAULT_NEW_ADVENTURE_ZH = '\u65b0\u5192\u9669'
const DEFAULT_MY_ADVENTURE_ZH = '\u6211\u7684\u5192\u9669'
const DEFAULT_AI_WORLD_ZH = 'AI \u751f\u6210\u7684\u4e16\u754c'
const DEFAULT_ADVENTURER_DE = 'Abenteurer'
const DEFAULT_NEW_ADVENTURE_DE = 'Neues Abenteuer'
const DEFAULT_MY_ADVENTURE_DE = 'Mein Abenteuer'
const DEFAULT_AI_WORLD_DE = 'KI-generierte Welt'

const router = useRouter()
const route = useRoute()
function canonicalLaunchSource(query: Record<string, unknown>): string {
  const adventure = typeof query.adventure_id === 'string' ? query.adventure_id : ''
  const module = typeof query.module_id === 'string' ? query.module_id : ''
  const selectedWorld = typeof query.world === 'string' ? query.world : ''
  return JSON.stringify(adventure ? ['adventure', module, adventure] : ['world', selectedWorld || 'direct'])
}
const capturedLaunchSource = canonicalLaunchSource(route.query)
const capturedLaunchSeed = typeof route.query.seed === 'string' ? route.query.seed.trim() : ''
const returnTo = computed(() => route.query.return_to === 'library' ? 'library' : 'overview')
const returnLabel = computed(() => returnTo.value === 'library'
  ? gameDefault('返回内容库', 'Back to Content Library', 'Zur Inhaltsbibliothek', 'コンテンツライブラリに戻る', 'Вернуться в библиотеку контента')
  : gameDefault('返回我的游戏', 'Back to My Games', 'Zu Meine Spiele', 'マイゲームに戻る', 'Вернуться в мои игры'))
const draftSource = computed(() => {
  return { source: capturedLaunchSource, seed: capturedLaunchSeed }
})
const draftReady = ref(false)
const restoringDraft = ref(false)
function navigateBack(): void { void router.push({ name: returnTo.value }) }
function sessionToken(): string | null {
  try {
    if (typeof localStorage === 'undefined') return null
    const storageKey = accessTokenStorageKey()
    const token = localStorage.getItem(storageKey)
    return token ? `${storageKey}\u0000${token}` : null
  } catch { return null }
}
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
const storyMode = ref<StoryMode>('prose')
const narrativePerspectiveTouched = ref(false)
const gmStyleFollowWorld = ref(true)
const gmStyle = ref<GmStyle>({ tone: '', verbosity: 'normal', pace: 'normal', custom_instructions: '' })
const advancementMode = ref<AdvancementMode>('milestone')
const advancementAuthority = ref<AdvancementAuthority>('ai_gm')
const gameLanguage = ref<Locale>(locale.value)
const customName = ref(''), customDesc = ref('')
const aiPrompt = ref(''), aiRule = ref('')
const aiAutoRule = ref(false), aiGeneratedRule = ref<GeneratedRuleResponse | null>(null)
const loreChoice = ref('__builtin__')
const selectedBookIds = ref<string[] | undefined>(undefined)
const availableLorebooks = ref<CreationLorebook[]>([])
const booksLoaded = ref(false)
const modules = ref<ModuleSummary[]>([])
const seed = ref(''), busy = ref(false), error = ref('')
const catalogueReady = ref(false)
const settingsChecked = ref(false)
const sceneImageFile = ref<File | null>(null)
const mapBackgroundChoice = ref('auto')
const mapBackgroundFile = ref<File | null>(null)
const defaultSceneImageUrl = ref(ruleSceneUrl())
const customSceneImageUrl = ref('')
// One seed per create draft: variant previews stay stable while catalogs load,
// but a fresh adventure can receive a different content default.
const sceneImageSelectionSeed = ref(`create-${Date.now()}-${Math.random().toString(36).slice(2)}`)

const ruleDetail = ref<RuleTemplate | null>(null)
const characters = ref<CreateCharacter[]>([])
const cards = ref<CharacterCard[]>([])
const showWizard = ref(false), showRulesetBuilder = ref(false), showPicker = ref(false)
const editIdx = ref<number | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const dfInput = ref<HTMLInputElement | null>(null)

const step = ref<Step>(1)
const stepTitleKeys = ['stepWorld', 'stepGameSettings', 'stepCharacters', 'stepConfirm'] as const
const stepProgress = computed(() => createStepProgress(step.value))
const activeRule = computed(() => mode.value === 'ai' ? (aiGeneratedRule.value?.rule_id || aiRule.value) : rule.value)
const activeRuleSummary = computed(() => rules.value.find(item => item.rule_id === activeRule.value))
const galRuleAvailable = computed(() => rules.value.some(item => item.rule_id === 'gal_narrative'))
const galPreview = computed<boolean>({
  get: () => playMode.value === 'gal',
  set(on: boolean) {
    selectPlayMode(on ? 'gal' : 'free')
    if (!on) return
    solo.value = true
    if (galRuleAvailable.value) rule.value = 'gal_narrative'
  },
})
const usesProfessionalBuilder = computed(() => (
  activeRuleSummary.value?.ruleset_runtime?.capabilities.character_builder === 'professional'
))
const supportsAdventurePackages = computed(() => Boolean(
  activeRuleSummary.value?.ruleset_runtime?.capabilities.adventure_formats?.length,
))
const showAdventurePackages = computed(() => (
  mode.value === 'template'
  && (supportsAdventurePackages.value || availableModuleAdventures.value.length > 0)
))
const adventureSelection = useAdventureSelection({ rule: activeRule, world, language: gameLanguage, mode, supported: supportsAdventurePackages, setRule: id => { rule.value = id } })
const { moduleId, playMode, selectedKey: adventureChoice, selectedAdventure, availableModuleAdventures, selectPlayMode, initializeLaunch, issue: adventureIssue } = adventureSelection
const adventureCompatibleRule = computed(() => {
  const adventure = selectedAdventure.value
  return adventure ? rules.value.find(rule => adventureMatchesRule(adventure, rule)) || null : null
})
const adventureRecommendedWorld = computed(() => selectedAdventure.value?.recommended_world_id || '')
const adventureWorldRequired = computed(() => selectedAdventure.value?.world_policy === 'fixed')
const adventureIssueGuide = computed(() => {
  const adventure = selectedAdventure.value
  const reasons = new Set(adventure?.incompatibility_reasons || [])
  if (reasons.has('ruleset_runtime_unavailable') || reasons.has('runtime_id_mismatch') || reasons.has('runtime_version_too_old') || reasons.has('adventure_format_unsupported')) {
    return gameDefault(
      `这个冒险包需要 ${adventure ? adventureRequirementLabel(adventure, locale.value) : '兼容的规则运行时'}，当前规则没有对应的冒险能力。请使用下方兼容规则，或先安装/启用对应规则来源。`,
      `This adventure package needs ${adventure ? adventureRequirementLabel(adventure, locale.value) : 'a compatible rules runtime'}, but the current ruleset does not provide its adventure capability. Use the compatible rules below, or enable the required rules source first.`,
      `Dieses Abenteuerpaket benötigt ${adventure ? adventureRequirementLabel(adventure, locale.value) : 'eine kompatible Regel-Laufzeit'}, aber das aktuelle Regelwerk bietet die Abenteuerfähigkeit nicht. Verwende unten ein kompatibles Regelwerk oder aktiviere zuerst die erforderliche Quelle.`,
    )
  }
  if (reasons.has('world_mismatch') || (adventureWorldRequired.value && adventureRecommendedWorld.value && !worlds.value.some(item => worldIdOf(item) === adventureRecommendedWorld.value))) {
    return gameDefault(
      `这个冒险包要求世界“${adventure?.recommended_world_id || '指定世界'}”。请使用推荐世界；如果这个世界尚未安装，请先到内容包页启用它。`,
      `This adventure package requires the world “${adventure?.recommended_world_id || 'the specified world'}”. Use the recommended world; if it is not installed, enable it on the Content packages page first.`,
      `Dieses Abenteuerpaket benötigt die Welt „${adventure?.recommended_world_id || 'die angegebene Welt'}“. Verwende die empfohlene Welt oder aktiviere sie zuerst unter Inhaltsquellen.`,
    )
  }
  return gameDefault(
    '请按冒险包标注的规则、世界和来源逐项调整。冒险包会保留在当前选择中；只有明确放弃它，才会继续自由对局。',
    'Adjust the rules, world, and source named by the package. The package stays selected; continue in free play only after explicitly leaving it.',
    'Passe Regeln, Welt und Quelle des Pakets an. Das Paket bleibt ausgewählt; wechsle nur nach ausdrücklichem Verlassen zum freien Spiel.',
  )
})
function applyAdventureRecommendedWorld(): void {
  if (adventureRecommendedWorld.value && worlds.value.some(item => worldIdOf(item) === adventureRecommendedWorld.value)) world.value = adventureRecommendedWorld.value
}
function applyAdventureCompatibleRule(): void {
  if (adventureCompatibleRule.value) rule.value = adventureCompatibleRule.value.rule_id
}
const activeWorldTemplate = computed(() => worlds.value.find(item => worldIdOf(item) === world.value))
const defaultSceneImagePool = computed<SceneImageRef[]>(() => {
  if (mode.value === 'template' && activeWorldTemplate.value) {
    const worldPool = sceneImageCandidates(activeWorldTemplate.value.scene_image, activeWorldTemplate.value.scene_images, false)
    if (worldPool.length) return worldPool
  }
  return sceneImageCandidates(
    activeRuleSummary.value?.scene_image || ruleDetail.value?.scene_image,
    activeRuleSummary.value?.scene_images || ruleDetail.value?.scene_images,
  )
})
const defaultSceneImageRef = computed<SceneImageRef | undefined>(() => pickSceneImageReference(
  defaultSceneImagePool.value[0],
  defaultSceneImagePool.value.slice(1),
  `${sceneImageSelectionSeed.value}:${world.value}:${activeRule.value}`,
))
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
async function chooseAdventure(event: Event): Promise<void> {
  if (!catalogueReady.value) return
  const key = String((event.target as HTMLSelectElement).value || '')
  const item = availableModuleAdventures.value.find(adventure => adventureKey(adventure) === key)
  if (item) await initializeLaunch({ module_id: moduleId.value, adventure_id: item.adventure_id }, rules.value, worlds.value)
}
function adventureDisabled(item: { required_runtime?: unknown; incompatibility_reasons?: string[] }): boolean {
  const reasons = item.incompatibility_reasons || []
  return !item.required_runtime || reasons.some(reason => reason !== 'ruleset_runtime_unavailable' && reason !== 'world_mismatch')
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
const loreSourceSummary = computed(() => {
  const language = locale.value.split('-')[0]
  const copy = {
    zh: { builtin: '沿用当前世界的资料册（可单独选择本局使用哪些）', blank: '新建独立世界（只有一册空白资料）', copied: '新建独立世界，引用「', copiedEnd: '」的资料册（仅引用，不会复制条目）' },
    en: { builtin: 'Use this world’s books (choose books for this game)', blank: 'Independent world (blank primary book)', copied: 'Independent world, reference source books: ', copiedEnd: ' (references only; entries are not copied)' },
    de: { builtin: 'Bücher dieser Welt verwenden (Auswahl für dieses Spiel)', blank: 'Eigene Welt (leeres Hauptbuch)', copied: 'Eigene Welt, Quellbücher referenzieren: ', copiedEnd: ' (nur Referenzen; Einträge werden nicht kopiert)' },
    ja: { builtin: 'この世界の資料集を使う（このゲームで使う冊を選択）', blank: '独立した世界（空白メイン資料集）', copied: '独立した世界で参照元の資料集を使う：', copiedEnd: '（参照のみ。項目はコピーされません）' },
    ru: { builtin: 'Использовать книги этого мира (выберите книги для игры)', blank: 'Отдельный мир (пустая основная книга)', copied: 'Отдельный мир со ссылками на исходные книги: ', copiedEnd: ' (только ссылки; записи не копируются)' },
  }[language] || {
    builtin: 'Use this world’s books (choose books for this game)', blank: 'Independent world (blank primary book)', copied: 'Independent world, reference source books: ', copiedEnd: ' (references only; entries are not copied)',
  }
  if (loreChoice.value === '__blank__') return copy.blank
  if (loreChoice.value.startsWith('copy:')) {
    const source = loreChoice.value.slice(5)
    const item = availableLoreWorlds.value.find(world => worldIdOf(world) === source)
    return `${copy.copied}${worldNameOf(item || { id: source })}${copy.copiedEnd}`
  }
  return copy.builtin
})
const lorePreviewWorldId = computed(() => mode.value === 'template' ? world.value : '')
watch([lorePreviewWorldId, loreChoice], () => {
  if (!restoringDraft.value) selectedBookIds.value = undefined
  booksLoaded.value = false
})
const loreManageHref = computed(() => {
  const source = loreChoice.value.startsWith('copy:') ? loreChoice.value.slice(5) : loreChoice.value === '__builtin__' ? lorePreviewWorldId.value : ''
  return source ? router.resolve({ name: 'lorebook', query: { world: source } }).href : ''
})
const apiReady = computed(() => isLlmConfigReady(settings.config))
const showApiSetupHint = computed(() => settingsChecked.value && !settings.error && !apiReady.value)

function worldIdOf(w: WorldTemplateSummary | WorldSummary): string { return String(w.world_id || w.id || '') }
function worldNameOf(w: WorldTemplateSummary | WorldSummary): string { return String(w.world_name || w.name || w.id || '') }
function worldLanguageLabel(w: WorldTemplateSummary | WorldSummary): string {
  const language = String((w as WorldTemplateSummary).active_locale || (w as WorldSummary).language || '').toLowerCase()
  return localeLabel(normalizeLocale(language))
}
function worldOptionLabel(w: WorldTemplateSummary | WorldSummary): string { return `${worldNameOf(w)} · ${worldLanguageLabel(w)}` }
function ruleNameOf(r: RuleSummary): string { return String(r.rule_name || r.rule_id) }
function ruleDescriptionOf(r: RuleSummary): string { return String(r.description || '') }
function cloneCharacter<T extends CharacterSheet>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }
function gameDefault(zh: string, en: string, de?: string, ja?: string, ru?: string): string {
  // Same fallback order as the backend: requested locale, then English, then
  // Chinese. Previously every locale except de/en fell straight to Chinese.
  const byLocale: Partial<Record<Locale, string>> = { 'zh-CN': zh, en, de, ja: ja || en, ru: ru || en }
  for (const candidate of localeChain(gameLanguage.value)) {
    const value = byLocale[candidate]
    if (value) return value
  }
  return en
}
function stepTitle(value: number): string { return t(stepTitleKeys[value - 1] || 'stepConfirm') }
function stepGuidance(value: number): string {
  const copy = {
    1: gameDefault('选择玩法与世界，规则和资料可按需调整。', 'Choose a play mode and world; adjust rules and books as needed.', 'Wähle Spielmodus und Welt; passe Regeln und Bücher bei Bedarf an.', '遊び方と世界を選び、必要に応じてルールと資料を変更します。', 'Выберите режим и мир; при необходимости измените правила и книги.'),
    2: gameDefault('设置这局的节奏、叙事视角与房间访问方式。', 'Set the pace, narrative perspective, and room access for this game.', 'Lege Tempo, Erzählperspektive und Raumzugang für dieses Spiel fest.', 'このゲームのテンポ、語り手の視点、ルームのアクセス方法を設定します。', 'Настройте темп, перспективу повествования и доступ к комнате.'),
    3: gameDefault('安排初始角色与席位控制方式，稍后仍可在游戏内管理。', 'Assemble the starting party and seat control; you can manage the party in game later.', 'Stelle die Startgruppe und Sitzsteuerung zusammen; die Gruppe kann später im Spiel verwaltet werden.', '初期キャラクターと席の操作方式を設定します。後からゲーム内で管理できます。', 'Соберите начальную группу и настройте управление местами; позже это можно изменить в игре.'),
    4: gameDefault('检查开局内容、规则和对局设置，确认后进入游戏。', 'Review the launch content, rules, and game settings before entering the game.', 'Prüfe Inhalte, Regeln und Spieleinstellungen, bevor du ins Spiel gehst.', '開始内容、ルール、ゲーム設定を確認してからゲームに入ります。', 'Проверьте содержимое старта, правила и настройки игры перед входом.'),
  }
  return copy[value as keyof typeof copy] || copy[4]
}
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

function draftSnapshot(): CreateDraftSnapshot {
  return {
    step: step.value, mode: mode.value, world: world.value, rule: rule.value, name: name.value,
    description: description.value, difficulty: difficulty.value, solo: solo.value, openRoom: openRoom.value,
    narrativePerspective: narrativePerspective.value, narrativePerspectiveTouched: narrativePerspectiveTouched.value,
    storyMode: storyMode.value,
    gmStyleFollowWorld: gmStyleFollowWorld.value, gmStyle: { ...gmStyle.value }, advancementMode: advancementMode.value,
    advancementAuthority: advancementAuthority.value, gameLanguage: gameLanguage.value, customName: customName.value,
    customDesc: customDesc.value, aiPrompt: aiPrompt.value, aiRule: aiRule.value, aiAutoRule: aiAutoRule.value, aiGeneratedRule: aiGeneratedRule.value ? JSON.parse(JSON.stringify(aiGeneratedRule.value)) as GeneratedRuleResponse : null,
    loreChoice: loreChoice.value, selectedBookIds: selectedBookIds.value === undefined ? undefined : [...selectedBookIds.value], moduleId: moduleId.value, playMode: playMode.value, adventureChoice: adventureChoice.value,
    seed: seed.value, characters: characters.value.map(character => cloneCharacter(character)), cardControl: [...cardControl.value],
    mapBackgroundChoice: mapBackgroundChoice.value, sceneImageFile: sceneImageFile.value, mapBackgroundFile: mapBackgroundFile.value,
    sceneImageSelectionSeed: sceneImageSelectionSeed.value,
  }
}

function restoreDraft(snapshot: CreateDraftSnapshot): void {
  step.value = snapshot.step; mode.value = snapshot.mode; world.value = snapshot.world; rule.value = snapshot.rule; name.value = snapshot.name
  description.value = snapshot.description; difficulty.value = snapshot.difficulty; solo.value = snapshot.solo; openRoom.value = snapshot.openRoom
  narrativePerspective.value = snapshot.narrativePerspective; narrativePerspectiveTouched.value = snapshot.narrativePerspectiveTouched
  storyMode.value = normalizeStoryMode(snapshot.storyMode)
  gmStyleFollowWorld.value = snapshot.gmStyleFollowWorld; gmStyle.value = { ...snapshot.gmStyle }
  advancementMode.value = snapshot.advancementMode; advancementAuthority.value = snapshot.advancementAuthority; gameLanguage.value = snapshot.gameLanguage as Locale
  customName.value = snapshot.customName; customDesc.value = snapshot.customDesc; aiPrompt.value = snapshot.aiPrompt; aiRule.value = snapshot.aiRule; aiAutoRule.value = snapshot.aiAutoRule; aiGeneratedRule.value = snapshot.aiGeneratedRule ? JSON.parse(JSON.stringify(snapshot.aiGeneratedRule)) as GeneratedRuleResponse : null
  loreChoice.value = snapshot.loreChoice; selectedBookIds.value = snapshot.selectedBookIds === undefined ? undefined : [...snapshot.selectedBookIds]; moduleId.value = snapshot.moduleId; playMode.value = snapshot.playMode; adventureChoice.value = snapshot.adventureChoice; seed.value = snapshot.seed
  characters.value = snapshot.characters.map(character => ensureCharacter(character)); cardControl.value = snapshot.cardControl as CardControlMode[]
  mapBackgroundChoice.value = snapshot.mapBackgroundChoice
  sceneImageFile.value = snapshot.sceneImageFile || null
  mapBackgroundFile.value = snapshot.mapBackgroundFile || null
  if (snapshot.sceneImageSelectionSeed) sceneImageSelectionSeed.value = snapshot.sceneImageSelectionSeed
}

watch(draftSnapshot, snapshot => {
  if (draftReady.value) saveCreateDraft(draftSource.value, snapshot)
}, { deep: true })
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
  if (restoringDraft.value) return
  if (!id) { ruleDetail.value = null; return }
  try {
    const rd = await api<RuleDetailResponse>(`/rules/${id}?language=${encodeURIComponent(gameLanguage.value)}`)
    ruleDetail.value = rd.rule || null
  } catch { ruleDetail.value = null }
}, { immediate: true })
watch([aiPrompt, aiRule, aiAutoRule], () => { if (!restoringDraft.value) aiGeneratedRule.value = null })
watch(usesProfessionalBuilder, (enabled) => {
  if (restoringDraft.value) return
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
watch(seed, (value) => { if (!restoringDraft.value && value.trim()) sceneImageFile.value = null })
watch(solo, (value) => {
  if (restoringDraft.value) return
  if (!narrativePerspectiveTouched.value) {
    narrativePerspective.value = value ? 'immersive' : 'third_person'
  }
})
watch(locale, (next) => { if (!restoringDraft.value) gameLanguage.value = next })
watch(gameLanguage, async (language, previous) => {
  if (restoringDraft.value) return
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
  if (restoringDraft.value) return
  if (world.value && availableWorlds.value.some(w => worldIdOf(w) === world.value)) return
  world.value = worldIdOf(availableWorlds.value[0] || worlds.value[0] || {})
})
watch(world, (worldId) => {
  if (restoringDraft.value) return
  if (mode.value !== 'template' || !worldId) return
  const defaultRule = String(worlds.value.find(item => worldIdOf(item) === worldId)?.default_rule || '')
  if (defaultRule && rules.value.some(item => item.rule_id === defaultRule)) rule.value = defaultRule
})
watch([gameLanguage, loreWorlds], () => {
  if (restoringDraft.value) return
  if (!loreChoice.value.startsWith('copy:')) return
  const selected = loreChoice.value.slice('copy:'.length)
  if (!availableLoreWorlds.value.some(w => worldIdOf(w) === selected)) loreChoice.value = '__builtin__'
})

onMounted(async () => {
  try {
    setCreateDraftSession(sessionToken())
    const savedDraft = loadCreateDraft(draftSource.value)
    if (savedDraft) {
      restoringDraft.value = true
      gameLanguage.value = savedDraft.gameLanguage as Locale
    }
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
    if (!savedDraft && !seed.value.trim()) {
      const defaults = { ...defaultGamePreferences(), ...settings.config.game_defaults }
      narrativePerspective.value = defaults.narrative_perspective === 'auto'
        ? (solo.value ? 'immersive' : 'third_person') : defaults.narrative_perspective
      narrativePerspectiveTouched.value = defaults.narrative_perspective !== 'auto'
      storyMode.value = defaults.story_mode
      gmStyleFollowWorld.value = defaults.gm_style_override === null
      if (defaults.gm_style_override) gmStyle.value = { ...defaults.gm_style_override }
      advancementMode.value = defaults.advancement_mode
      advancementAuthority.value = defaults.advancement_authority
    }
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
    // World/rule refs in this handoff are UI defaults; creation validates them again.
    // Establish the normal first-seat shape before restoring a draft. This keeps
    // professional and legacy builders from replacing restored characters.
    characters.value = usesProfessionalBuilder.value
      ? []
      : [{ character_name: gameDefault(DEFAULT_ADVENTURER_ZH, 'Adventurer', DEFAULT_ADVENTURER_DE), background: '', identity: {}, attributes: {}, skills: [] }]
    await adventureSelection.initializeLaunch(route.query, rules.value, worlds.value)
    if (savedDraft) {
      restoreDraft(savedDraft)
      await nextTick()
      restoringDraft.value = false
    }
    void moduleApi.list().then(result => { modules.value = result.modules || [] }).catch(() => {})
    catalogueReady.value = true
    draftReady.value = true
  } catch (e: unknown) {
    error.value = errorMessage(e)
  }
})

onBeforeUnmount(() => {
  // Re-check auth/backend scope when leaving so logout, account switch, or
  // standalone endpoint changes cannot make an old in-memory draft eligible.
  setCreateDraftSession(sessionToken())
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
function cancelRulesetBuilder() {
  const idx = editIdx.value
  if (idx !== null) {
    const current = characters.value[idx]
    const canonical = current?.ruleset_character
    // Imported/legacy cards are staged before the review builder opens. If the
    // player cancels without producing a canonical blueprint, discard that
    // staged card so Create cannot submit an invalid professional payload.
    if (!canonical || typeof canonical !== 'object' || Array.isArray(canonical)) {
      characters.value.splice(idx, 1)
      cardControl.value = removeCardControl(cardControl.value, idx)
    }
  }
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
// 卡内角色世界书是否随卡一起导入；不勾就真的只导入卡（后端 include_character_book=false）。
const importCardLore = ref(true)
async function importCardFile(file: File) {
  const r = await importTavernCard(file, { target: 'character_card', includeCharacterBook: importCardLore.value })
  const card = r.card
  if (!card) throw new Error(t('importFailed'))
  cards.value.push(card)
  const character = characterFromCard(card)
  characters.value.push(character)
  if (r.lorebook) {
    toast.success(t('importedCharacterWithLore', {
      name: card.character_name,
      book: r.lorebook.name || r.lorebook_book_id || '',
      count: r.lorebook.entries ?? 0,
    }))
  }
  if (cardNeedsReview(card)) {
    editIdx.value = characters.value.length - 1
    if (usesProfessionalBuilder.value) showRulesetBuilder.value = true
    else showWizard.value = true
    toast.info(t('cardRuleConversionReview'))
  } else if (!r.lorebook) {
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
  if (!catalogueReady.value && !seed.value.trim()) return false
  if (mode.value === 'template' && !seed.value.trim() && !booksLoaded.value) return false
  return canAdvanceCreateStep({ step: step.value, seed: seed.value, activeRule: activeRule.value, playMode: playMode.value, adventureReady: adventureSelection.ready.value, mode: mode.value, customName: customName.value, aiPrompt: aiPrompt.value, characterNames: characters.value.map(character => character.character_name || '') })
}
function requireApiConfiguration() {
  if (!showApiSetupHint.value) return
  throw new Error(t('apiSetupRequired'))
}
function throwMutationError(response: { ok?: boolean; error?: string; error_code?: string }) {
  if (response.ok || !response.error) return
  const code = String(response.error_code || '')
  const localized = code === 'PROFESSIONAL_CHARACTER_BLUEPRINT_REQUIRED'
    ? gameDefault(
      '这个专业角色卡缺少规则数据。请重新打开当前规则的专业建卡器，完成建卡后再创建或加入。旧角色卡不能仅凭展示属性安全转换。',
      'This professional character card is missing its rules data. Reopen the current professional character builder, finish it, and then create or join the game. Legacy cards cannot be converted from display fields alone.',
    )
    : ''
  throw new Error(localized || response.error)
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
  throwMutationError(r)
  if (!r.rule_id) throw new Error(t('missingRuleId'))
  aiGeneratedRule.value = r
  const all = await api<RulesResponse>('/rules').catch(() => null)
  if (all?.rules) rules.value = all.rules
  toast.success(`${t('generatedRuleToast')}${r.rule_name || r.rule_id}`)
}
async function nextStep() {
  if (!canNext() || step.value >= 4) return
  if (step.value === 3 && reviewIncompleteProfessionalCard()) return
  busy.value = true; error.value = ''
  try {
    if (step.value === 1) await prepareAiRule()
    step.value = (step.value + 1) as Step
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
function prevStep() { if (step.value > 1) step.value = (step.value - 1) as Step }

function reviewIncompleteProfessionalCard(): boolean {
  if (!usesProfessionalBuilder.value) return false
  const index = characters.value.findIndex(character => {
    const blueprint = character.ruleset_character
    return !blueprint || typeof blueprint !== 'object' || Array.isArray(blueprint)
  })
  if (index < 0) return false
  step.value = 3
  error.value = i18n.global.t('apiErrors.PROFESSIONAL_CHARACTER_BLUEPRINT_REQUIRED')
  openWizard(index)
  return true
}

async function create() {
  if (busy.value) return
  if (reviewIncompleteProfessionalCard()) return
  busy.value = true; error.value = ''
  try {
    requireApiConfiguration()
    const passwordProblem = !seed.value.trim() && !openRoom.value ? roomPasswordProblem(roomPassword.value) : null
    if (passwordProblem) {
      const [key, params] = roomPasswordProblemMessage(passwordProblem)
      throw new Error(t(key, params))
    }
    const players = characters.value.map((c, i) => {
      const card = cloneCharacter(c)
      // 每张卡在「角色」步骤都有明确的控制方式，创建 payload 直接带上它；
      // 服务端把它写成 players[uid].control.mode（human / ai / unclaimed），
      // 前端不自己造 AI 状态，也不修改 control revision。
      return { ...card, control: cardControlAt(cardControl.value, i) }
    })
    // The preview and the saved game should use the same concrete member of
    // the pool.  The picker is deterministic for this draft and the backend
    // still validates/materializes the reference.  A seed restart keeps the
    // original save cover unless the user explicitly uploads a replacement.
    const selectedSceneImage = sceneImageFile.value
      ? await uploadSceneImage(sceneImageFile.value)
      : (seed.value.trim() ? undefined : defaultSceneImageRef.value)
    if (seed.value.trim()) {
      const r = await api<GameMutationResponse>('/games/create-from-seed', { method: 'POST', body: JSON.stringify({ seed_code: seed.value.trim(), solo: solo.value, players, language: gameLanguage.value, scene_image: selectedSceneImage, narrative_perspective: narrativePerspective.value, story_mode: storyMode.value }) })
      throwMutationError(r)
      if (!r.game_key) throw new Error(t('missingGameId'))
      clearCreateDraft(draftSource.value)
      rememberCurrentGame(r.game_key, r.world_name || '')
      await router.push({ name: 'play', query: { game: r.game_key } }); return
    }
    if (mode.value === 'template' && !booksLoaded.value) throw new Error(gameDefault('资料册尚未确认，请返回第一步重试。', 'Books are not confirmed. Return to the first step and retry.'))
    const selectedMapBackground = mapBackgroundFile.value
      ? await uploadMapBackground(mapBackgroundFile.value)
      : mapBackgroundSelection(mapBackgroundChoice.value)
    if (playMode.value === 'adventure' && !adventureSelection.ready.value) throw new Error(t(adventureIssue.value || 'adventureLaunchIncompatible'))
    const payload: Record<string, unknown> = applyStoryMode({ solo: solo.value, difficulty: difficulty.value, rule_id: activeRule.value, play_mode: playMode.value, ...adventureSelection.bindingPayload.value, description: description.value, room_password: openRoom.value ? '' : (roomPassword.value.trim() || null), players, language: gameLanguage.value, scene_image: selectedSceneImage, map_background: selectedMapBackground, narrative_perspective: narrativePerspective.value, gm_style_override: gmStyleFollowWorld.value ? null : { ...gmStyle.value }, advancement_mode: supportsAdvancementPolicy.value ? advancementMode.value : 'milestone', advancement_authority: supportsAdvancementPolicy.value ? advancementAuthority.value : 'ai_gm' }, storyMode.value)
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
      throwMutationError(gw)
      worldId = gw.world_id; payload.world_id = worldId; payload.game_name = gw.world_name || gameDefault(DEFAULT_AI_WORLD_ZH, 'AI Generated World', DEFAULT_AI_WORLD_DE)
    }
    const lorePayload = applyLorebookSource(payload, loreChoice.value as LorebookSource, worldId, loreWorlds.value, undefined, {
      blank: gameDefault(' · 独立世界副本（空白资料册）', ' · Independent world copy (blank lorebook)', ' · Eigenständige Weltkopie (leeres Weltenbuch)'),
      copied: gameDefault(' · 独立世界（引用资料册）', ' · Independent world (referenced book)', ' · Eigene Welt (Buchverweis)', ' · 独立した世界（資料集参照）', ' · Отдельный мир (ссылка на книгу)'),
    }, mode.value === 'template' && booksLoaded.value ? selectedBookIds.value : undefined, availableLorebooks.value)
    const r = await api<GameMutationResponse>('/games/create', { method: 'POST', body: JSON.stringify(lorePayload) })
    throwMutationError(r)
    if (!r.game_key) throw new Error(t('missingGameId'))
    if (r.generated_password) window.alert(t('roomPasswordGenerated', { pwd: r.generated_password }))
    rememberCurrentGame(r.game_key, r.world_name || String(lorePayload.game_name || ''))
    clearCreateDraft(draftSource.value)
    await router.push({ name: 'play', query: { game: r.game_key } })
  } catch (e: unknown) {
    error.value = errorMessage(e)
    toast.error(error.value)
  } finally { busy.value = false }
}
</script>

<template>
  <section class="view create-page reference-create-page" :style="sceneImageStyle(selectedSceneImageUrl)" :aria-busy="!catalogueReady">
    <header class="create-page-header">
      <button class="create-back" :aria-label="returnLabel" :title="returnLabel" @click="navigateBack">←</button>
      <div><span class="create-page-header__eyebrow">NEW GAME</span><h1>{{ t('createTitle') }}</h1></div>
    </header>

    <div class="create-wizard-shell">
      <aside class="create-step-rail" :aria-label="t('createTitle')">
        <div class="create-step-rail__sigil" aria-hidden="true">✦</div>
        <div v-for="item in stepProgress" :key="item.number" :class="['create-step-rail__item', item.state, { 'last-step': item.number === 4 }]" :aria-current="item.state === 'current' ? 'step' : undefined">
          <span class="create-progress-marker">{{ item.state === 'complete' ? '✓' : item.number }}</span>
          <span><strong>{{ t(item.key) }}</strong><small>STEP 0{{ item.number }}</small></span>
        </div>
        <section class="create-rail-summary" :aria-label="t('stepConfirm')">
          <div><span>{{ t('gameMode') }}</span><strong>{{ solo ? t('solo') : t('multiplayer') }}</strong></div>
          <div><span>{{ t('rule') }}</span><strong>{{ activeRuleSummary ? ruleNameOf(activeRuleSummary) : t('selectRule') }}</strong></div>
          <div><span>{{ t('charactersCount') }}</span><strong data-testid="create-rail-party-count">{{ characters.length }}</strong></div>
        </section>
      </aside>
      <main class="create-stage-panel">
        <div v-if="showApiSetupHint" class="notice create-api-hint">
          <div><strong>{{ t('apiSetupRequiredTitle') }}</strong><p>{{ t('apiSetupRequiredHint') }}</p></div>
          <button type="button" class="primary" @click="router.push({ name: 'settings' })">{{ t('goToApiSettings') }}</button>
        </div>

        <header class="create-stage-head">
          <span aria-hidden="true">0{{ step }}</span>
          <div class="create-stage-head-copy"><h2>{{ stepTitle(step) }}</h2><p class="create-step-guidance">{{ stepGuidance(step) }}</p></div>
        </header>

        <p v-if="!catalogueReady && !error" class="notice" role="status">{{ t('galleryLoading') }}</p>
        <fieldset class="create-ready-fields" :disabled="!catalogueReady">
        <section v-if="step === 1" class="create-step-card create-content-stage">
          <template v-if="!seed">
            <section v-if="showAdventurePackages" class="create-play-mode">
                  <span class="create-field-title">{{ gameDefault('玩法模式', 'Play mode', 'Spielmodus', '遊び方', 'Режим игры') }}</span>
                  <div class="narrative-perspective-cards">
                    <button type="button" :class="{ active: playMode === 'free' }" :aria-pressed="playMode === 'free'" @click="selectPlayMode('free')">
                      <strong>{{ gameDefault('自由冒险', 'Free adventure', 'Freies Abenteuer', '自由な冒険', 'Свободное приключение') }}</strong>
                      <small>{{ gameDefault('选择世界，由 GM 自由推进故事。', 'Choose a world and let the GM guide the story.', 'Wähle eine Welt; der GM führt die Geschichte.', '世界を選び、GM が物語を進めます。', 'Выберите мир; мастер ведёт историю.') }}</small>
                    </button>
                    <button type="button" :class="{ active: playMode === 'adventure' }" :aria-pressed="playMode === 'adventure'" @click="selectPlayMode('adventure')">
                      <strong>{{ gameDefault('模组冒险', 'Adventure module', 'Abenteuermodul', '冒険モジュール', 'Модуль приключения') }}</strong>
                      <small>{{ gameDefault('选择模组，按预设剧情与场景游玩。', 'Choose a module with prepared scenes and story.', 'Wähle ein Modul mit vorbereiteten Szenen.', 'モジュールの物語と場面に沿って遊びます。', 'Играйте по подготовленным сценам модуля.') }}</small>
                    </button>
                  </div>
                  <div v-if="playMode === 'adventure'" class="create-adventure-select-row">
                    <select v-if="modules.length" v-model="moduleId" :aria-label="gameDefault('内容包来源（可选）', 'Content package source (optional)', 'Inhaltspaketquelle (optional)', 'コンテンツパックの出所（任意）', 'Источник пакета контента (необязательно)')">
                      <option value="">{{ gameDefault('不使用内容包（内置冒险）', 'No content package (built-in adventures)', 'Kein Inhaltspaket (integrierte Abenteuer)', 'コンテンツパックなし（内蔵冒険）', 'Без пакета контента (встроенные приключения)') }}</option>
                      <option v-for="item in modules" :key="item.id" :value="item.id">{{ item.name }}</option>
                    </select>
                    <select :aria-label="gameDefault('冒险模组', 'Adventure module', 'Abenteuermodul', '冒険モジュール', 'Модуль приключения')" :value="adventureChoice" :disabled="!catalogueReady" @change="chooseAdventure">
                      <option value="" disabled>{{ gameDefault('请选择冒险包', 'Choose an adventure package', 'Abenteuerpaket wählen') }}</option>
                      <option v-for="item in availableModuleAdventures" :key="adventureKey(item)" :value="adventureKey(item)" :disabled="adventureDisabled(item)">
                        {{ item.name }} · {{ item.estimated_minutes }} {{ gameDefault('分钟', 'min', 'Min.') }}{{ item.compatibility !== 'compatible' ? gameDefault('（需匹配推荐世界）', ' (requires its recommended world)', ' (benötigt die empfohlene Welt)') : '' }}
                      </option>
                    </select>
                    <a class="create-manage-adventures" :href="adventureManagerHref" target="_blank" rel="noopener">{{ t('manageAdventurePackages') }}</a>
                  </div>
            </section>
            <div v-if="selectedAdventure && playMode === 'adventure'" class="create-selected-adventure">
              <AdventureLaunchSummary :adventure="selectedAdventure" :rule-name="activeRuleSummary?.rule_name" />
            </div>
            <div v-if="playMode !== 'adventure'" class="create-world-source">
              <span class="create-field-title">{{ gameDefault('世界来源', 'World source', 'Weltquelle', '世界の作成方法', 'Источник мира') }}</span>
              <div class="create-mode-cards create-mode-segmented">
              <button type="button" :class="{ active: mode === 'template' }" :aria-pressed="mode === 'template'" @click="mode = 'template'"><b>◇</b><strong>{{ t('modeTemplate') }}</strong><small>{{ gameDefault('使用已有世界与规则。', 'Use an existing world and ruleset.', 'Verwende eine vorhandene Welt und Regeln.', '既存の世界とルールを使う。', 'Использовать готовый мир и правила.') }}</small></button>
              <button type="button" :class="{ active: mode === 'custom' }" :aria-pressed="mode === 'custom'" @click="mode = 'custom'"><b>✎</b><strong>{{ t('modeCustom') }}</strong><small>{{ gameDefault('从自己的世界背景开始。', 'Start from your own world background.', 'Beginne mit deinem eigenen Welthintergrund.', '自分の世界設定から始める。', 'Начать с собственного фона мира.') }}</small></button>
              <button type="button" :class="{ active: mode === 'ai' }" :aria-pressed="mode === 'ai'" @click="mode = 'ai'"><b>✦</b><strong>{{ t('modeAi') }}</strong><small>{{ gameDefault('让 AI 生成世界草案。', 'Draft a world with AI.', 'Erstelle einen Weltentwurf mit KI.', 'AI に世界のたたき台を作ってもらう。', 'Создать черновик мира с помощью ИИ.') }}</small></button>
            </div>
            </div>
            <div class="create-config-surface">
              <template v-if="mode === 'template'">
                <label><span>{{ t('worldTemplate') }}</span><select v-model="world"><option v-for="w in availableWorlds" :key="worldIdOf(w)" :value="worldIdOf(w)">{{ worldOptionLabel(w) }}</option></select></label>
                <label><span>{{ t('adventureName') }}</span><input v-model="name" :placeholder="t('useWorldName')"></label>

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
              <details v-if="mode !== 'ai'" class="wide create-rule-settings">
                <summary class="create-setup-summary"><span>{{ t('rule') }}</span><strong>{{ ruleNameOf(activeRuleSummary || { rule_id: activeRule }) }}</strong><span class="create-setup-adjust">{{ gameDefault('调整', 'Adjust', 'Anpassen', '変更', 'Изменить') }} <span aria-hidden="true">⌄</span></span></summary>
                <div class="create-rule-settings-body">
                <label><span>{{ t('rule') }}</span><select v-model="rule"><option v-for="r in rules" :key="r.rule_id" :value="r.rule_id">{{ ruleNameOf(r) }}</option></select></label>
                <label v-if="galRuleAvailable" class="wide create-check create-gal-preview">
                  <input type="checkbox" v-model="galPreview">
                  <span>{{ gameDefault('GAL 预览（实验性）', 'GAL preview (experimental)', 'GAL-Vorschau (experimentell)') }}</span>
                  <small>{{ gameDefault('单机视觉小说式对话：无骰子无属性，使用现有聊天界面游玩。实验功能，尚无好感度、路线与结局。', 'Solo visual-novel style play: no dice or stats, played in the existing chat UI. Experimental; no affection, routes or endings yet.', 'Solo-Visual-Novel-Dialog: ohne Würfel und Werte, in der bestehenden Chat-Oberfläche. Experimentell; noch ohne Zuneigung, Routen oder Enden.') }}</small>
                </label>
                <details v-if="recommendedRulesList.length" open class="create-recommended-rules">
                  <summary>{{ t('recommendedRules') }}</summary>
                  <div class="rec-grid">
                    <button v-for="r in recommendedRulesList" :key="r.rule_id" type="button" :class="['rec-card', { active: rule === r.rule_id }]" @click="rule = r.rule_id">
                      <strong>{{ ruleNameOf(r) }}</strong>
                      <span class="recommendation-badges"><small :class="{ professional: isProfessionalRule(r) }">{{ recommendationBadge(r) }}</small><small v-if="isDndAdvancedRule(r)" class="beta-badge">{{ gameDefault('测试版', 'Beta', 'Beta') }}</small></span>
                      <p>{{ ruleDescriptionOf(r) }}</p>
                    </button>
                  </div>
                  <small class="rec-hint">{{ t('recommendedHint') }}</small>
                </details>
                </div>
              </details>
              <WorldLorebookSummary v-model="selectedBookIds" v-model:books="availableLorebooks" class="wide" :world-id="lorePreviewWorldId" :source="loreChoice" :manage-href="loreManageHref" @ready="booksLoaded = $event">
                <template #source-settings>
                  <LorebookSourcePicker v-model="loreChoice" :worlds="availableLoreWorlds.map(w => ({ id: worldIdOf(w), name: worldNameOf(w), language: worldLanguageLabel(w) }))" />
                </template>
              </WorldLorebookSummary>

            </div>
          </template>
          <details class="wide create-advanced-settings create-launch-advanced" :open="Boolean(seed.trim())">
            <summary>{{ gameDefault('语言、种子与补充背景', 'Language, seed, and extra background', 'Sprache, Seed und zusätzlicher Hintergrund', '言語、シード、追加背景', 'Язык, сид и дополнительный фон') }}</summary>
            <div class="create-field-grid">
              <label><span>{{ t('gameLanguage') }}</span><select v-model="gameLanguage"><option v-for="code in SUPPORTED_LOCALES" :key="code" :value="code">{{ localeLabel(code) }}</option></select><small>{{ t('gameLanguageHint') }}</small></label>
              <label><span>{{ t('seedCode') }}</span><input v-model="seed" :placeholder="t('seedPlaceholder')"><small>{{ t('restoreBySeed') }}</small></label>
              <label class="wide"><span>{{ t('extraBackground') }}</span><textarea v-model="description" rows="4" :placeholder="t('extraBackgroundPlaceholder')"></textarea><small>{{ gameDefault('这里填写世界背景；可选的冒险剧情会作为独立的冒险节点绑定。', 'Describe the world background here. Optional adventure plots are bound separately as adventure nodes.', 'Beschreibe hier den Welthintergrund. Optionale Abenteuerhandlung wird separat als Abenteuerknoten gebunden.') }}</small></label>
            </div>
          </details>
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
            <fieldset class="wide narrative-perspective-field" data-testid="create-story-mode">
              <legend>{{ t('storyMode') }}</legend>
              <div class="narrative-perspective-cards">
                <button type="button" :class="{ active: storyMode === 'prose' }" :aria-pressed="storyMode === 'prose'" @click="storyMode = 'prose'">
                  <strong>{{ t('storyModeProse') }}</strong><small>{{ t('storyModeProseHint') }}</small>
                </button>
                <button type="button" :class="{ active: storyMode === 'dialogue' }" :aria-pressed="storyMode === 'dialogue'" @click="storyMode = 'dialogue'">
                  <strong>{{ t('storyModeDialogue') }}</strong><small>{{ t('storyModeDialogueHint') }}</small>
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
            <label class="create-import-lore"><input v-model="importCardLore" type="checkbox" class="tavern-include-lore"> {{ t('tavernImportIncludeLore') }}</label>
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
          <div class="create-confirm-context">
            <div class="create-confirm-section-head"><span>01</span><div><strong>{{ gameDefault('开局内容', 'Launch context', 'Startkontext', '開始内容', 'Начальные данные') }}</strong><small>{{ gameDefault('这几项共同决定本局从哪里开始。', 'These choices define where this game starts.', 'Diese Auswahl bestimmt, wo dieses Spiel beginnt.', 'これらの選択で、このゲームの開始内容が決まります。', 'Эти параметры определяют, с чего начнётся игра.') }}</small></div></div>
            <div class="create-confirm-grid">
              <article><span>{{ t('world') }}</span><strong>{{ confirmationWorld }}</strong><small>{{ loreSourceSummary }}</small></article>
              <article><span>{{ t('rule') }}</span><strong>{{ ruleNameOf(rules.find(r => r.rule_id === activeRule) || { rule_id: activeRule }) }}</strong><small>{{ gameDefault('本局的检定与行动遵循这些规则。', 'Checks and actions in this game follow these rules.', 'Proben und Aktionen in diesem Spiel folgen diesen Regeln.', 'このゲームの判定と行動はこれらのルールに従います。', 'Проверки и действия в этой игре следуют этим правилам.') }}</small></article>
              <article v-if="supportsAdventurePackages"><span>{{ gameDefault('冒险模式', 'Adventure mode', 'Abenteuermodus') }}</span><strong>{{ selectedAdventure?.name || gameDefault('标准自由对局', 'Standard free play', 'Standard-Freispiel') }}</strong></article>
            </div>
          </div>
          <div class="create-confirm-settings">
            <div class="create-confirm-section-head"><span>02</span><div><strong>{{ gameDefault('对局设置', 'Game settings', 'Spieleinstellungen', 'ゲーム設定', 'Настройки игры') }}</strong><small>{{ gameDefault('创建后会按这些设置进入游戏。', 'The game will open with these settings.', 'Das Spiel öffnet sich mit diesen Einstellungen.', 'ゲームはこれらの設定で開始されます。', 'Игра откроется с этими настройками.') }}</small></div></div>
            <div class="create-confirm-grid">
              <article><span>{{ t('gameMode') }}</span><strong>{{ solo ? t('solo') : t('multiplayer') }}</strong></article>
              <article><span>{{ t('narrativePerspective') }}</span><strong>{{ narrativePerspective === 'immersive' ? t('narrativeImmersive') : t('narrativeThirdPerson') }}</strong></article>
              <article><span>{{ t('storyMode') }}</span><strong>{{ storyMode === 'dialogue' ? t('storyModeDialogue') : t('storyModeProse') }}</strong></article>
              <article v-if="supportsAdvancementPolicy && !seed.trim()"><span>{{ t('advancementMode') }}</span><strong>{{ advancementMode === 'milestone' ? t('advancementMilestone') : t('advancementXp') }} · {{ advancementAuthority === 'ai_gm' ? t('advancementAiGm') : t('advancementHumanGm') }}</strong></article>
              <article><span>{{ t('difficulty') }}</span><strong>{{ difficulty === DIFFICULTY_EASY ? t('easy') : difficulty === DIFFICULTY_HARDCORE ? t('hardcore') : t('normal') }}</strong></article>
              <article><span>{{ t('roomOpen') }}</span><strong>{{ openRoom ? gameDefault('公开房间', 'Open room', 'Offener Raum') : gameDefault('需要邀请或密码', 'Invite or password', 'Einladung oder Passwort') }}</strong></article>
            </div>
          </div>
          <div class="create-confirm-party">
            <div class="create-confirm-section-head"><span>03</span><div><strong>{{ t('charactersCount') }} · {{ characters.length }}</strong><small>{{ gameDefault('控制方式已在角色步骤中保存，以下摘要会随角色一并进入本局。', 'Seat control is saved in the Characters step and carried into this game with each character.', 'Die Sitzsteuerung wird im Schritt Charaktere gespeichert und mit jeder Figur in dieses Spiel übernommen.', '操作方式はキャラクターの手順で保存され、各キャラクターと一緒にこのゲームへ引き継がれます。', 'Управление сохраняется на шаге персонажей и переносится в игру вместе с каждым персонажем.') }}</small></div></div>
            <div class="create-confirm-characters"><span v-for="(c, i) in characters" :key="i">{{ c.character_name }} · {{ controlLabel(controlOf(i)) }}</span></div>
          </div>
        </section>

        <p v-if="adventureIssue === 'adventureSelectionRequired'" class="notice" role="status">{{ t(adventureIssue) }}</p>
        <div v-else-if="adventureIssue" class="error-banner" role="alert">
          <p>{{ t(adventureIssue) }}</p>
          <p class="adventure-issue-guide">{{ adventureIssueGuide }}</p>
          <div class="adventure-issue-actions">
            <button v-if="adventureCompatibleRule" type="button" @click="applyAdventureCompatibleRule">{{ gameDefault('使用兼容规则', 'Use compatible rules', 'Kompatible Regeln verwenden') }}</button>
            <button v-if="adventureRecommendedWorld" type="button" @click="applyAdventureRecommendedWorld">{{ gameDefault('使用推荐世界', 'Use recommended world', 'Empfohlene Welt verwenden') }}</button>
            <button type="button" @click="selectPlayMode('free')">{{ gameDefault('放弃冒险包，继续自由对局', 'Leave package and continue free play', 'Paket verlassen und frei spielen') }}</button>
            <a class="create-manage-adventures" :href="adventureManagerHref" target="_blank" rel="noopener">{{ gameDefault('打开冒险工作台检查来源', 'Open the adventure workbench to check the source', 'Öffne die Abenteuerwerkstatt und prüfe die Quelle') }}</a>
          </div>
        </div>
        <p v-if="error" class="error-banner">{{ error }}</p>
        <footer class="create-actions"><button @click="navigateBack">{{ t('cancel') }}</button><button v-if="step > 1" @click="prevStep">{{ t('previous') }}</button><button v-if="step < 4" class="primary" :disabled="busy || !canNext()" @click="nextStep">{{ busy && step === 1 ? t('preparing') : t('next') }} →</button><button v-else class="primary" :disabled="busy" @click="create">{{ busy ? t('creating') : t('createAndEnter') }} →</button></footer>
        </fieldset>
      </main>
    </div>

    <CharacterWizard v-if="showWizard" :rule-meta="ruleDetail" :rule-attrs="ruleAttrs" :attr-total="attrTotal" :skill-pool="skillPool" :rule-id="activeRule" :language="gameLanguage" :initial="editIdx !== null ? characters[editIdx] : undefined" @submit="onWizardSubmit" @cancel="showWizard = false" />
    <RulesetExperienceHost v-if="showRulesetBuilder" :rule-id="activeRule" :language="gameLanguage" :initial="editIdx !== null ? characters[editIdx] : undefined" @submit="onRulesetSubmit" @cancel="cancelRulesetBuilder" />
    <CharacterCardPicker v-if="showPicker" :cards="cards" :target-rule-id="activeRule" @pick="onPickerPick" @close="showPicker = false" />
  </section>
</template>
