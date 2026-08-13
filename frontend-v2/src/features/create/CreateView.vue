<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import type { CharacterCard, CharacterCardsResponse, CharacterSheet, GameMutationResponse, GeneratedRuleResponse, GeneratedWorldResponse, RuleDetailResponse, RuleSummary, RuleTemplate, RulesResponse, SceneImageRef, WorldListResponse, WorldSummary, WorldTemplateSummary, WorldTemplatesResponse } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useLocale, type Locale } from '@/composables/useLocale'
import CharacterWizard from '@/components/admin/CharacterWizard.vue'
import CharacterCardPicker from '@/components/admin/CharacterCardPicker.vue'
import PortraitImage from '@/components/PortraitImage.vue'
import AdventureSceneImagePicker from '@/components/common/AdventureSceneImagePicker.vue'
import MapBackgroundPicker from '@/components/common/MapBackgroundPicker.vue'
import { importTavernCard } from '@/utils/characterImport'
import { rememberCurrentGame } from '@/stores/gameContext'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { contentLanguageOf, filterByContentLanguage } from '@/utils/contentLanguage'
import { characterCardNeedsConversion } from '@/utils/characterCards'
import { ruleSceneUrl } from '@/composables/useBackgroundImages'
import { resolveSceneImageUrl, revokeSceneImageUrl, sceneImageStyle, uploadSceneImage } from '@/api/sceneImages'
import { mapBackgroundSelection, uploadMapBackground } from '@/api/mapBackgrounds'

interface CreateCharacter extends CharacterSheet { character_name: string }
type CreateMode = 'template' | 'custom' | 'ai'
type Step = 1 | 2 | 3
const DIFFICULTY_EASY = '\u8f7b\u677e'
const DIFFICULTY_NORMAL = '\u6807\u51c6'
const DIFFICULTY_HARDCORE = '\u786c\u6838'
const DEFAULT_ADVENTURER_ZH = '\u5192\u9669\u8005'
const DEFAULT_NEW_ADVENTURE_ZH = '\u65b0\u5192\u9669'
const DEFAULT_MY_ADVENTURE_ZH = '\u6211\u7684\u5192\u9669'
const DEFAULT_AI_WORLD_ZH = 'AI \u751f\u6210\u7684\u4e16\u754c'
const BLANK_LOREBOOK_SUFFIX_ZH = '\uff08\u7a7a\u767d\u4e16\u754c\u4e66\uff09'
const COPIED_LOREBOOK_SUFFIX_ZH = '\uff08\u590d\u5236\u4e16\u754c\u4e66\uff09'

const router = useRouter()
const toast = useToast()
const { locale, t } = useLocale()
const settings = useSettingsStore()

const worlds = ref<WorldTemplateSummary[]>([])
const rules = ref<RuleSummary[]>([])
const loreWorlds = ref<WorldSummary[]>([])
const mode = ref<CreateMode>('template')
const world = ref(''), rule = ref(''), name = ref(''), description = ref('')
const difficulty = ref(DIFFICULTY_NORMAL), solo = ref(true), roomPassword = ref(''), openRoom = ref(false)
const gameLanguage = ref<Locale>(locale.value)
const customName = ref(''), customDesc = ref('')
const aiPrompt = ref(''), aiRule = ref('')
const aiAutoRule = ref(false), aiGeneratedRule = ref<GeneratedRuleResponse | null>(null)
const loreChoice = ref('__builtin__')
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
const showWizard = ref(false), showPicker = ref(false)
const editIdx = ref<number | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const dfInput = ref<HTMLInputElement | null>(null)

const step = ref<Step>(1)
const activeRule = computed(() => mode.value === 'ai' ? (aiGeneratedRule.value?.rule_id || aiRule.value) : rule.value)
const activeRuleSummary = computed(() => rules.value.find(item => item.rule_id === activeRule.value))
const activeWorldTemplate = computed(() => worlds.value.find(item => worldIdOf(item) === world.value))
const defaultSceneImageRef = computed<SceneImageRef | undefined>(() => {
  if (mode.value === 'template' && activeWorldTemplate.value?.scene_image) return activeWorldTemplate.value.scene_image
  return activeRuleSummary.value?.scene_image || ruleDetail.value?.scene_image
})
const selectedSceneImageUrl = computed(() => customSceneImageUrl.value || defaultSceneImageUrl.value || ruleSceneUrl(activeRule.value))
const languageMatchedWorlds = computed(() => filterByContentLanguage(worlds.value, gameLanguage.value))
const availableWorlds = computed(() => languageMatchedWorlds.value.length ? languageMatchedWorlds.value : worlds.value)
const availableLoreWorlds = computed(() => filterByContentLanguage(loreWorlds.value, gameLanguage.value))
const ruleAttrs = computed(() => ruleDetail.value?.attributes || [])
const skillPool = computed(() => ruleDetail.value?.skill_pool || ruleDetail.value?.skills || [])
const attrTotal = computed(() => ruleDetail.value?.attribute_points || 60)
const selectedTemplateWorldName = computed(() => worldNameOf(worlds.value.find(item => worldIdOf(item) === world.value) || {}))
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
const apiReady = computed(() => Boolean(
  String(settings.config.base_url || '').trim()
  && String(settings.config.model || '').trim()
  && settings.config.api_key?.configured,
))
const showApiSetupHint = computed(() => settingsChecked.value && !settings.error && !apiReady.value)

function worldIdOf(w: WorldTemplateSummary | WorldSummary): string { return String(w.world_id || w.id || '') }
function worldNameOf(w: WorldTemplateSummary | WorldSummary): string { return String(w.world_name || w.name || w.id || '') }
function worldLanguageLabel(w: WorldTemplateSummary | WorldSummary): string { return contentLanguageOf(w) === 'en' ? t('english') : t('chinese') }
function worldOptionLabel(w: WorldTemplateSummary | WorldSummary): string { return `${worldNameOf(w)} · ${worldLanguageLabel(w)}` }
function ruleNameOf(r: RuleSummary): string { return gameLanguage.value === 'en' ? String(r.rule_name_en || r.rule_name || r.rule_id) : (r.rule_name || r.rule_id) }
function cloneCharacter<T extends CharacterSheet>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }
function gameDefault(zh: string, en: string): string { return gameLanguage.value === 'en' ? en : zh }
function ensureCharacter(value: CharacterSheet): CreateCharacter {
  return { ...value, character_name: String(value.character_name || gameDefault(DEFAULT_ADVENTURER_ZH, 'Adventurer')) }
}

watch(activeRule, async (id) => {
  if (!id) { ruleDetail.value = null; return }
  try {
    const rd = await api<RuleDetailResponse>(`/rules/${id}`)
    ruleDetail.value = rd.rule || null
  } catch { ruleDetail.value = null }
}, { immediate: true })
watch([aiPrompt, aiRule, aiAutoRule], () => { aiGeneratedRule.value = null })
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
watch(locale, (next) => { gameLanguage.value = next })
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
    api<WorldTemplatesResponse>('/world-templates'),
    api<RulesResponse>('/rules'),
    api<WorldListResponse>('/worlds'),
    api<CharacterCardsResponse>('/character-cards'),
    settingsPromise,
  ])
  worlds.value = w.templates || []
  rules.value = r.rules || []
  loreWorlds.value = lw.worlds || []
  cards.value = cs.cards || []
  world.value = worldIdOf(availableWorlds.value[0] || worlds.value[0] || {})
  const worldDefaultRule = String(activeWorldTemplate.value?.default_rule || '')
  rule.value = rules.value.some(item => item.rule_id === worldDefaultRule)
    ? worldDefaultRule
    : (rules.value[0]?.rule_id || '')
  aiRule.value = rule.value
  characters.value = [{ character_name: gameDefault(DEFAULT_ADVENTURER_ZH, 'Adventurer'), background: '', identity: {}, attributes: {}, skills: [] }]
})

onBeforeUnmount(() => {
  revokeSceneImageUrl(defaultSceneImageUrl.value)
  revokeSceneImageUrl(customSceneImageUrl.value)
})

function openWizard(idx: number | null) {
  editIdx.value = idx
  showWizard.value = true
}
function onWizardSubmit(c: CharacterSheet) {
  const character = ensureCharacter(c)
  if (editIdx.value !== null) characters.value[editIdx.value] = character
  else characters.value.push(character)
  showWizard.value = false
  editIdx.value = null
}
function onPickerPick(c: CharacterCard) {
  const character = ensureCharacter({
    character_name: c.character_name,
    background: c.background || '',
    identity: c.identity || {},
    attributes: c.attributes || {},
    skills: c.skills || [],
    equipment: c.equipment || [],
    inventory: c.inventory || [],
    key_items: c.key_items || [],
    gold: c.gold || 0,
    currency: c.currency,
    race: c.race,
    class: c.class,
    portrait: c.portrait,
  })
  characters.value.push(character)
  showPicker.value = false
  if (characterCardNeedsConversion(c, activeRule.value)) {
    editIdx.value = characters.value.length - 1
    showWizard.value = true
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
  characters.value.push(ensureCharacter({
    character_name: card.character_name,
    background: card.background || '',
    identity: card.identity || {},
    attributes: card.attributes || {},
    skills: card.skills || [],
    portrait: card.portrait,
  }))
  toast.success(t('importedCharacter', { name: card.character_name }))
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
}

function canNext() {
  if (step.value === 1) {
    if (seed.value.trim()) return true
    if (!activeRule.value) return false
    if (mode.value === 'ai' && !aiPrompt.value.trim()) return false
    if (mode.value === 'custom' && !customName.value.trim()) return false
    return true
  }
  if (step.value === 2) return characters.value.length >= 1 && characters.value.every(c => c.character_name?.trim())
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
  if (!canNext() || step.value >= 3) return
  busy.value = true; error.value = ''
  try {
    if (step.value === 1) await prepareAiRule()
    step.value = (step.value + 1) as Step
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
function prevStep() { if (step.value > 1) step.value = (step.value - 1) as Step }

async function create() {
  busy.value = true; error.value = ''
  try {
    requireApiConfiguration()
    const players = characters.value.map(cloneCharacter)
    const selectedSceneImage = sceneImageFile.value ? await uploadSceneImage(sceneImageFile.value) : undefined
    if (seed.value.trim()) {
      const r = await api<GameMutationResponse>('/games/create-from-seed', { method: 'POST', body: JSON.stringify({ seed_code: seed.value.trim(), solo: solo.value, players, language: gameLanguage.value, scene_image: selectedSceneImage }) })
      if (!r.ok && r.error) throw new Error(r.error)
      if (!r.game_key) throw new Error(t('missingGameId'))
      rememberCurrentGame(r.game_key, r.world_name || '')
      router.push({ name: 'play', query: { game: r.game_key } }); return
    }
    const selectedMapBackground = mapBackgroundFile.value
      ? await uploadMapBackground(mapBackgroundFile.value)
      : mapBackgroundSelection(mapBackgroundChoice.value)
    const payload: Record<string, unknown> = { solo: solo.value, difficulty: difficulty.value, rule_id: activeRule.value, description: description.value, room_password: openRoom.value ? '' : (roomPassword.value.trim() || null), players, language: gameLanguage.value, scene_image: selectedSceneImage, map_background: selectedMapBackground }
    let worldId = ''
    if (mode.value === 'template') {
      worldId = world.value; payload.world_id = worldId
      payload.game_name = name.value || worldNameOf(worlds.value.find(w => worldIdOf(w) === world.value) || {}) || gameDefault(DEFAULT_NEW_ADVENTURE_ZH, 'New Adventure')
    } else if (mode.value === 'custom') {
      worldId = 'custom_' + Date.now(); payload.world_id = worldId
      payload.world_name = customName.value.trim() || gameDefault(DEFAULT_MY_ADVENTURE_ZH, 'My Adventure'); payload.custom_world = true; payload.description = customDesc.value
    } else if (mode.value === 'ai') {
      if (!aiPrompt.value.trim()) throw new Error(t('enterWorldPrompt'))
      if (aiAutoRule.value && !aiGeneratedRule.value?.rule_id) await prepareAiRule()
      const selectedRule = activeRule.value
      payload.rule_id = selectedRule
      const gw = await api<GeneratedWorldResponse>('/generate-world', { method: 'POST', body: JSON.stringify({ prompt: aiPrompt.value, rule_id: selectedRule, language: gameLanguage.value }) })
      if (!gw.ok && gw.error) throw new Error(gw.error)
      worldId = gw.world_id; payload.world_id = worldId; payload.game_name = gw.world_name || gameDefault(DEFAULT_AI_WORLD_ZH, 'AI Generated World')
    }
    if (loreChoice.value === '__builtin__') payload.create_lorebook = false
    else if (loreChoice.value === '__blank__') {
      payload.source_world_id = worldId; payload.world_id = worldId + '_blank_' + Date.now()
      payload.game_name = String(payload.game_name || '') + gameDefault(BLANK_LOREBOOK_SUFFIX_ZH, ' (Blank Lorebook)'); payload.create_lorebook = true; payload.blank_lorebook = true
    } else if (loreChoice.value.startsWith('copy:')) {
      const src = loreChoice.value.slice(5)
      payload.source_world_id = worldId; payload.world_id = worldId + '_copy_' + Date.now()
      payload.game_name = String(payload.game_name || '') + gameDefault(COPIED_LOREBOOK_SUFFIX_ZH, ' (Copied Lorebook)'); payload.create_lorebook = true; payload.lorebook_world_id = src
    }
    const r = await api<GameMutationResponse>('/games/create', { method: 'POST', body: JSON.stringify(payload) })
    if (!r.ok && r.error) throw new Error(r.error)
    if (!r.game_key) throw new Error(t('missingGameId'))
    if (r.generated_password) window.alert(t('roomPasswordGenerated', { pwd: r.generated_password }))
    rememberCurrentGame(r.game_key, r.world_name || String(payload.game_name || ''))
    router.push({ name: 'play', query: { game: r.game_key } })
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
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
          <div v-for="n in 3" :key="n" :class="['create-step-nav-item', { active: step === n, done: step > n }]">
            <span>{{ step > n ? '✓' : n }}</span>
            <div><strong>{{ n === 1 ? t('stepWorld') : n === 2 ? t('stepCharacters') : t('stepConfirm') }}</strong><small>STEP 0{{ n }}</small></div>
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
          <div><h2>{{ step === 1 ? t('stepWorld') : step === 2 ? t('stepCharacters') : t('stepConfirm') }}</h2><p>{{ t('createSubtitle') }}</p></div>
        </header>

        <section v-if="step === 1" class="create-step-card">
          <div class="create-field-grid create-field-grid-compact">
            <label><span>{{ t('gameLanguage') }}</span><select v-model="gameLanguage"><option value="zh-CN">{{ t('chinese') }}</option><option value="en">{{ t('english') }}</option></select><small>{{ t('gameLanguageHint') }}</small></label>
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
              <label class="wide"><span>{{ t('extraBackground') }}</span><textarea v-model="description" rows="4" :placeholder="t('extraBackgroundPlaceholder')"></textarea></label>
              <label><span>{{ t('gameMode') }}</span><select v-model.number="solo"><option :value="true">{{ t('solo') }}</option><option :value="false">{{ t('multiplayer') }}</option></select></label>
              <label><span>{{ t('difficulty') }}</span><select v-model="difficulty"><option :value="DIFFICULTY_EASY">{{ t('easy') }}</option><option :value="DIFFICULTY_NORMAL">{{ t('normal') }}</option><option :value="DIFFICULTY_HARDCORE">{{ t('hardcore') }}</option></select></label>
              <label class="wide"><span>{{ t('roomPassword') }}</span><input v-model="roomPassword" :placeholder="t('roomPasswordPlaceholder')"></label>
              <label class="wide checkbox"><input type="checkbox" v-model="openRoom"><span>{{ t('roomOpen') }}</span></label>
              <AdventureSceneImagePicker v-model="sceneImageFile" class="wide" :default-url="defaultSceneImageUrl" />
              <details class="wide create-advanced-settings">
                <summary>{{ t('mapBackgroundAdvanced') }}</summary>
                <MapBackgroundPicker v-model="mapBackgroundChoice" v-model:file="mapBackgroundFile" />
              </details>
            </div>
          </template>
        </section>

        <section v-else-if="step === 2" class="create-step-card create-character-stage">
          <div class="create-character-actions">
            <button class="primary" @click="openWizard(null)">＋ {{ t('newCharacter') }}</button><button @click="showPicker = true">{{ t('pickFromLibrary') }}</button><button @click="dfInput?.click()">{{ t('importDiceframeCard') }}</button><button @click="fileInput?.click()">{{ t('importStCard') }}</button>
            <input ref="dfInput" type="file" accept=".json,application/json" hidden @change="onImportDfCard"><input ref="fileInput" type="file" accept=".png,.json" hidden @change="onStImport">
          </div>
          <div class="create-character-grid">
            <article v-for="(c, i) in characters" :key="i" class="create-character-card">
              <PortraitImage :portrait="c.portrait" :rule-id="activeRule" :seed="c.character_name || String(i)" :name="c.character_name" :size="72" />
              <div><h3>{{ c.character_name || t('unnamed') }}</h3><p>{{ c.identity?.origin || c.race || '' }} · {{ c.identity?.archetype || c.class || '' }}</p><small>{{ c.skills?.length || 0 }} {{ t('skills') }}</small></div>
              <div class="actions"><button @click="openWizard(i)">{{ t('edit') }}</button><button class="danger" @click="removeCharacter(i)">{{ t('remove') }}</button></div>
            </article>
            <button class="create-character-empty" @click="openWizard(null)"><b>＋</b><span>{{ t('newCharacter') }}</span></button>
          </div>
        </section>

        <section v-else class="create-step-card create-confirm-stage">
          <div class="create-confirm-cover" :style="sceneImageStyle(selectedSceneImageUrl)"><span>✦</span><h2>{{ t('confirmCreate') }}</h2><p>{{ confirmationName }}</p></div>
          <div class="create-confirm-grid">
            <article><span>{{ t('world') }}</span><strong>{{ confirmationWorld }}</strong></article>
            <article><span>{{ t('rule') }}</span><strong>{{ ruleNameOf(rules.find(r => r.rule_id === activeRule) || { rule_id: activeRule }) }}</strong></article>
            <article><span>{{ t('difficulty') }}</span><strong>{{ difficulty === DIFFICULTY_EASY ? t('easy') : difficulty === DIFFICULTY_HARDCORE ? t('hardcore') : t('normal') }}</strong></article>
            <article><span>{{ t('charactersCount') }}</span><strong>{{ characters.length }}</strong></article>
          </div>
          <div class="create-confirm-characters"><span v-for="(c, i) in characters" :key="i">{{ c.character_name }}</span></div>
        </section>

        <p v-if="error" class="error-banner">{{ error }}</p>
        <footer class="create-actions"><button @click="router.push({ name: 'overview' })">{{ t('cancel') }}</button><button v-if="step > 1" @click="prevStep">{{ t('previous') }}</button><button v-if="step < 3" class="primary" :disabled="busy || !canNext()" @click="nextStep">{{ busy && step === 1 ? t('preparing') : t('next') }} →</button><button v-else class="primary" :disabled="busy" @click="create">{{ busy ? t('creating') : t('createAndEnter') }} →</button></footer>
      </main>
    </div>

    <CharacterWizard v-if="showWizard" :rule-meta="ruleDetail" :rule-attrs="ruleAttrs" :attr-total="attrTotal" :skill-pool="skillPool" :rule-id="activeRule" :language="gameLanguage" :initial="editIdx !== null ? characters[editIdx] : undefined" @submit="onWizardSubmit" @cancel="showWizard = false" />
    <CharacterCardPicker v-if="showPicker" :cards="cards" :target-rule-id="activeRule" @pick="onPickerPick" @close="showPicker = false" />
  </section>
</template>
