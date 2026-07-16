<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import type { CharacterCard, CharacterCardsResponse, CharacterSheet, GameMutationResponse, GeneratedRuleResponse, GeneratedWorldResponse, RuleDetailResponse, RuleSummary, RuleTemplate, RulesResponse, WorldListResponse, WorldSummary, WorldTemplateSummary, WorldTemplatesResponse } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useLocale, type Locale } from '@/composables/useLocale'
import CharacterWizard from '@/components/admin/CharacterWizard.vue'
import CharacterCardPicker from '@/components/admin/CharacterCardPicker.vue'
import { importTavernCard } from '@/utils/characterImport'
import { rememberCurrentGame } from '@/stores/gameContext'

interface CreateCharacter extends CharacterSheet { character_name: string }
type CreateMode = 'template' | 'custom' | 'ai'
type Step = 1 | 2 | 3

const router = useRouter()
const toast = useToast()
const { locale, t } = useLocale()

const worlds = ref<WorldTemplateSummary[]>([])
const rules = ref<RuleSummary[]>([])
const loreWorlds = ref<WorldSummary[]>([])
const mode = ref<CreateMode>('template')
const world = ref(''), rule = ref(''), name = ref(''), description = ref('')
const difficulty = ref('标准'), solo = ref(true), roomPassword = ref('')
const gameLanguage = ref<Locale>(locale.value)
const customName = ref(''), customDesc = ref('')
const aiPrompt = ref(''), aiRule = ref('')
const aiAutoRule = ref(false), aiGeneratedRule = ref<GeneratedRuleResponse | null>(null)
const loreChoice = ref('__builtin__')
const seed = ref(''), busy = ref(false), error = ref('')

const ruleDetail = ref<RuleTemplate | null>(null)
const characters = ref<CreateCharacter[]>([])
const cards = ref<CharacterCard[]>([])
const showWizard = ref(false), showPicker = ref(false)
const editIdx = ref<number | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const step = ref<Step>(1)
const activeRule = computed(() => mode.value === 'ai' ? (aiGeneratedRule.value?.rule_id || aiRule.value) : rule.value)
const languageMatchedWorlds = computed(() => worlds.value.filter(w => gameLanguage.value === 'en' ? w.language === 'en' : w.language !== 'en'))
const availableWorlds = computed(() => languageMatchedWorlds.value.length ? languageMatchedWorlds.value : worlds.value)
const ruleAttrs = computed(() => ruleDetail.value?.attributes || [])
const skillPool = computed(() => ruleDetail.value?.skill_pool || ruleDetail.value?.skills || [])
const attrTotal = computed(() => ruleDetail.value?.attribute_points || 60)

function worldIdOf(w: WorldTemplateSummary | WorldSummary): string { return String(w.world_id || w.id || '') }
function worldNameOf(w: WorldTemplateSummary | WorldSummary): string { return String(w.world_name || w.name || w.id || '') }
function ruleNameOf(r: RuleSummary): string { return r.rule_name || r.rule_id }
function cloneCharacter<T extends CharacterSheet>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }
function ensureCharacter(value: CharacterSheet): CreateCharacter {
  return { ...value, character_name: String(value.character_name || (gameLanguage.value === 'en' ? 'Adventurer' : '冒险者')) }
}

watch(activeRule, async (id) => {
  if (!id) { ruleDetail.value = null; return }
  try {
    const rd = await api<RuleDetailResponse>(`/rules/${id}`)
    ruleDetail.value = rd.rule || null
  } catch { ruleDetail.value = null }
}, { immediate: true })
watch([aiPrompt, aiRule, aiAutoRule], () => { aiGeneratedRule.value = null })
watch(locale, (next) => { gameLanguage.value = next })
watch([gameLanguage, worlds], () => {
  if (world.value && availableWorlds.value.some(w => worldIdOf(w) === world.value)) return
  world.value = worldIdOf(availableWorlds.value[0] || worlds.value[0] || {})
})

onMounted(async () => {
  const [w, r, lw, cs] = await Promise.all([
    api<WorldTemplatesResponse>('/world-templates'),
    api<RulesResponse>('/rules'),
    api<WorldListResponse>('/worlds'),
    api<CharacterCardsResponse>('/character-cards'),
  ])
  worlds.value = w.templates || []
  rules.value = r.rules || []
  loreWorlds.value = lw.worlds || []
  cards.value = cs.cards || []
  world.value = worldIdOf(availableWorlds.value[0] || worlds.value[0] || {})
  rule.value = rules.value[0]?.rule_id || ''
  aiRule.value = rule.value
  characters.value = [{ character_name: gameLanguage.value === 'en' ? 'Adventurer' : '冒险者', background: '', identity: {}, attributes: {}, skills: [] }]
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
  characters.value.push(ensureCharacter({
    character_name: c.character_name,
    background: c.background || '',
    identity: c.identity || {},
    attributes: c.attributes || {},
    skills: c.skills || [],
    gold: c.gold || 0,
    currency: c.currency,
    race: c.race,
    class: c.class,
  }))
  showPicker.value = false
  toast.success(t('addedFromLibrary'))
}
async function onStImport(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const card = await importTavernCard(file)
    cards.value.push(card)
    characters.value.push(ensureCharacter({
      character_name: card.character_name,
      background: card.background || '',
      identity: card.identity || {},
      attributes: card.attributes || {},
      skills: card.skills || [],
    }))
    toast.success(t('importedCharacter', { name: card.character_name }))
  } catch (err: unknown) { toast.error(errorMessage(err)) }
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
async function prepareAiRule() {
  if (mode.value !== 'ai' || !aiAutoRule.value || aiGeneratedRule.value?.rule_id) return
  if (!aiPrompt.value.trim()) throw new Error(t('enterWorldPrompt'))
  toast.info(t('generatingRule'))
  const r = await api<GeneratedRuleResponse>('/generate-rule', {
    method: 'POST',
    body: JSON.stringify({ prompt: aiPrompt.value, source_rule_id: aiRule.value }),
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
    const players = characters.value.map(cloneCharacter)
    if (seed.value.trim()) {
      const r = await api<GameMutationResponse>('/games/create-from-seed', { method: 'POST', body: JSON.stringify({ seed_code: seed.value.trim(), solo: solo.value, players, language: gameLanguage.value }) })
      if (!r.ok && r.error) throw new Error(r.error)
      if (!r.game_key) throw new Error(t('missingGameId'))
      rememberCurrentGame(r.game_key, r.world_name || '')
      router.push({ name: 'play', query: { game: r.game_key } }); return
    }
    const payload: Record<string, unknown> = { solo: solo.value, difficulty: difficulty.value, rule_id: activeRule.value, description: description.value, room_password: roomPassword.value, players, language: gameLanguage.value }
    let worldId = ''
    if (mode.value === 'template') {
      worldId = world.value; payload.world_id = worldId
      payload.game_name = name.value || worldNameOf(worlds.value.find(w => worldIdOf(w) === world.value) || {}) || (gameLanguage.value === 'en' ? 'New Adventure' : '新冒险')
    } else if (mode.value === 'custom') {
      worldId = 'custom_' + Date.now(); payload.world_id = worldId
      payload.world_name = customName.value.trim() || (gameLanguage.value === 'en' ? 'My Adventure' : '我的冒险'); payload.custom_world = true; payload.description = customDesc.value
    } else if (mode.value === 'ai') {
      if (!aiPrompt.value.trim()) throw new Error(t('enterWorldPrompt'))
      if (aiAutoRule.value && !aiGeneratedRule.value?.rule_id) await prepareAiRule()
      const selectedRule = activeRule.value
      payload.rule_id = selectedRule
      const gw = await api<GeneratedWorldResponse>('/generate-world', { method: 'POST', body: JSON.stringify({ prompt: aiPrompt.value, rule_id: selectedRule, language: gameLanguage.value }) })
      if (!gw.ok && gw.error) throw new Error(gw.error)
      worldId = gw.world_id; payload.world_id = worldId; payload.game_name = gw.world_name || (gameLanguage.value === 'en' ? 'AI Generated World' : 'AI 生成的世界')
    }
    if (loreChoice.value === '__builtin__') payload.create_lorebook = false
    else if (loreChoice.value === '__blank__') {
      payload.source_world_id = worldId; payload.world_id = worldId + '_blank_' + Date.now()
      payload.game_name = String(payload.game_name || '') + (gameLanguage.value === 'en' ? ' (Blank Lorebook)' : '（空白世界书）'); payload.create_lorebook = true; payload.blank_lorebook = true
    } else if (loreChoice.value.startsWith('copy:')) {
      const src = loreChoice.value.slice(5)
      payload.source_world_id = worldId; payload.world_id = worldId + '_copy_' + Date.now()
      payload.game_name = String(payload.game_name || '') + (gameLanguage.value === 'en' ? ' (Copied Lorebook)' : '（复制世界书）'); payload.create_lorebook = true; payload.lorebook_world_id = src
    }
    const r = await api<GameMutationResponse>('/games/create', { method: 'POST', body: JSON.stringify(payload) })
    if (!r.ok && r.error) throw new Error(r.error)
    if (!r.game_key) throw new Error(t('missingGameId'))
    rememberCurrentGame(r.game_key, r.world_name || String(payload.game_name || ''))
    router.push({ name: 'play', query: { game: r.game_key } })
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>

<template>
  <section class="view narrow create-page">
    <header class="view-title create-hero">
      <div>
        <h1>{{ t('createTitle') }}</h1>
        <p>{{ t('createSubtitle') }}</p>
      </div>
    </header>

    <div class="wizard-steps">
      <span v-for="n in 3" :key="n" :class="['wizard-step', { active: step === n, done: step > n }]">
        {{ n === 1 ? t('stepWorld') : n === 2 ? t('stepCharacters') : t('stepConfirm') }}
      </span>
    </div>

    <div v-if="step === 1" class="form create-step-card">
      <label>{{ t('gameLanguage') }}
        <select v-model="gameLanguage">
          <option value="zh-CN">{{ t('chinese') }}</option>
          <option value="en">{{ t('english') }}</option>
        </select>
      </label>
      <p class="form-hint">{{ t('gameLanguageHint') }}</p>
      <label>{{ t('seedCode') }}<input v-model="seed" :placeholder="t('seedPlaceholder')"></label>
      <template v-if="!seed">
        <div class="mode-tabs">
          <button type="button" :class="{ active: mode === 'template' }" @click="mode = 'template'">{{ t('modeTemplate') }}</button>
          <button type="button" :class="{ active: mode === 'custom' }" @click="mode = 'custom'">{{ t('modeCustom') }}</button>
          <button type="button" :class="{ active: mode === 'ai' }" @click="mode = 'ai'">{{ t('modeAi') }}</button>
        </div>
        <template v-if="mode === 'template'">
          <label>{{ t('worldTemplate') }}
            <select v-model="world"><option v-for="w in availableWorlds" :key="w.world_id" :value="w.world_id">{{ w.world_name || w.world_id }}</option></select>
          </label>
          <label>{{ t('adventureName') }}<input v-model="name" :placeholder="t('useWorldName')"></label>
        </template>
        <template v-else-if="mode === 'custom'">
          <label>{{ t('customWorldName') }}<input v-model="customName" :placeholder="t('customWorldPlaceholder')"></label>
          <label>{{ t('worldDescription') }}<textarea v-model="customDesc" rows="4" :placeholder="t('worldDescriptionPlaceholder')"></textarea></label>
        </template>
        <template v-else-if="mode === 'ai'">
          <label>{{ t('aiWorldDescription') }}<textarea v-model="aiPrompt" rows="5" :placeholder="t('aiWorldPlaceholder')"></textarea></label>
          <label>{{ t('baseRule') }}<select v-model="aiRule"><option v-for="r in rules" :key="r.rule_id" :value="r.rule_id">{{ r.rule_name || r.rule_id }}</option></select></label>
          <label class="check-row"><input type="checkbox" v-model="aiAutoRule"> {{ t('aiRuleDraft') }}</label>
          <p v-if="aiGeneratedRule?.rule_id" class="notice">{{ t('generatedRule') }}{{ aiGeneratedRule.rule_name || aiGeneratedRule.rule_id }}{{ t('generatedRuleHint') }}</p>
        </template>
        <label v-if="mode !== 'ai'">{{ t('rule') }}<select v-model="rule"><option v-for="r in rules" :key="r.rule_id" :value="r.rule_id">{{ r.rule_name || r.rule_id }}</option></select></label>
        <label>{{ t('extraBackground') }}<textarea v-model="description" rows="3" :placeholder="t('extraBackgroundPlaceholder')"></textarea></label>
        <label>{{ t('lorebookSource') }}
          <select v-model="loreChoice">
            <option value="__builtin__">{{ t('builtinLorebook') }}</option>
            <option value="__blank__">{{ t('blankLorebook') }}</option>
            <option v-for="w in loreWorlds" :key="w.id" :value="'copy:' + w.id">{{ t('copyFrom') }}{{ w.name }}</option>
          </select>
        </label>
        <div class="two-cols">
          <label>{{ t('gameMode') }}<select v-model.number="solo"><option :value="true">{{ t('solo') }}</option><option :value="false">{{ t('multiplayer') }}</option></select></label>
          <label>{{ t('difficulty') }}<select v-model="difficulty"><option value="轻松">{{ t('easy') }}</option><option value="标准">{{ t('normal') }}</option><option value="硬核">{{ t('hardcore') }}</option></select></label>
        </div>
        <label>{{ t('roomPassword') }}<input v-model="roomPassword" :placeholder="t('roomPasswordPlaceholder')"></label>
      </template>
    </div>

    <div v-else-if="step === 2" class="form create-step-card">
      <div class="char-list">
        <article v-for="(c, i) in characters" :key="i" class="char-row">
          <div>
            <h3>{{ c.character_name || t('unnamed') }}</h3>
            <p class="muted">
              {{ c.identity?.origin || c.race || '' }} {{ c.identity?.archetype || c.class || '' }}
              · {{ c.skills?.length || 0 }} {{ t('skills') }}
            </p>
          </div>
          <div class="actions">
            <button @click="openWizard(i)">{{ t('edit') }}</button>
            <button @click="removeCharacter(i)">{{ t('remove') }}</button>
          </div>
        </article>
      </div>
      <div class="char-add">
        <button class="primary" @click="openWizard(null)">{{ t('newCharacter') }}</button>
        <button @click="showPicker = true">{{ t('pickFromLibrary') }}</button>
        <button @click="fileInput?.click()">{{ t('importStCard') }}</button>
        <input ref="fileInput" type="file" accept=".png,.json" hidden @change="onStImport">
      </div>
    </div>

    <div v-else class="form create-step-card">
      <h2>{{ t('confirmCreate') }}</h2>
      <p v-if="seed">{{ t('restoreBySeed') }}</p>
      <template v-else>
        <p><strong>{{ t('world') }}：</strong>{{ mode === 'template' ? (worlds.find(w => w.world_id === world)?.world_name || world) : mode === 'custom' ? customName : t('byAi') }}</p>
        <p><strong>{{ t('rule') }}：</strong>{{ rules.find(r => r.rule_id === activeRule)?.rule_name || activeRule }}</p>
        <p><strong>{{ t('difficulty') }}：</strong>{{ difficulty === '轻松' ? t('easy') : difficulty === '硬核' ? t('hardcore') : t('normal') }} · {{ solo ? t('solo') : t('multiplayer') }} · {{ gameLanguage === 'en' ? t('english') : t('chinese') }}</p>
      </template>
      <p><strong>{{ t('charactersCount') }}（{{ characters.length }}）：</strong></p>
      <ul>
        <li v-for="(c, i) in characters" :key="i">{{ c.character_name }}</li>
      </ul>
    </div>

    <p v-if="error" class="error-banner">{{ error }}</p>
    <div class="actions create-actions">
      <button @click="router.push({ name: 'overview' })">{{ t('cancel') }}</button>
      <button v-if="step > 1" @click="prevStep">{{ t('previous') }}</button>
      <button v-if="step < 3" class="primary" :disabled="busy || !canNext()" @click="nextStep">{{ busy && step === 1 ? t('preparing') : t('next') }}</button>
      <button v-else class="primary" :disabled="busy" @click="create">{{ busy ? t('creating') : t('createAndEnter') }}</button>
    </div>

    <CharacterWizard
      v-if="showWizard"
      :rule-meta="ruleDetail"
      :rule-attrs="ruleAttrs"
      :attr-total="attrTotal"
      :skill-pool="skillPool"
      :rule-id="activeRule"
      :initial="editIdx !== null ? characters[editIdx] : undefined"
      @submit="onWizardSubmit"
      @cancel="showWizard = false"
    />
    <CharacterCardPicker
      v-if="showPicker"
      :cards="cards"
      @pick="onPickerPick"
      @close="showPicker = false"
    />
  </section>
</template>
