<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorMessage } from '@/api/client'
import type { CharacterSheet, CharacterSkill, CharacterItem, GeneratedCharacterResponse, RuleMeta, SkillSpec } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useLocale } from '@/composables/useLocale'
import SkillEditor from './SkillEditor.vue'
import ItemEditor from './ItemEditor.vue'
import {
  identitySchema, identityLabel, attrDisplayName, currencyLabel,
  isAutoHpRule, calcAutoHp, setIdentityUpdate, suggestedAttributes, skillPointCost, localizedField,
  type IdentityField, type RuleAttr,
} from '@/utils/ruleSchema'

interface CharacterSubmit extends CharacterSheet { character_name: string }

type SheetUpdate = Partial<CharacterSheet> & { identity?: Record<string, string> }

const props = defineProps<{
  ruleMeta?: RuleMeta | null
  ruleAttrs: RuleAttr[]
  attrTotal: number
  skillPool?: Array<string | SkillSpec>
  ruleId?: string
  gameKey?: string
  language?: string
  initial?: CharacterSheet
}>()
const emit = defineEmits<{ submit: [character: CharacterSubmit]; cancel: [] }>()

const toast = useToast()
const { locale, t } = useLocale()
const step = ref<1 | 2 | 3 | 4>(1)

const characterName = ref('')
const identityValues = ref<Record<string, string>>({})
const identityFields = computed<IdentityField[]>(() =>
  identitySchema(props.ruleMeta).filter(f => f.key !== 'background')
)

const aiPrompt = ref('')
const generating = ref(false)

const attrs = ref<Record<string, number>>({})
const attrSum = computed(() =>
  Object.values(attrs.value).reduce((s, v) => s + (parseInt(String(v)) || 0), 0)
)
const attrPoints = computed(() => Math.max(props.attrTotal, attrSum.value) - attrSum.value)
const overLimit = computed(() => attrSum.value > props.attrTotal)
const attrHint = computed(() => localizedField<string>(props.ruleMeta, 'attr_hint') || '')
const diceHint = computed(() =>
  props.ruleMeta?.mechanics === 'dnd5e_core'
    ? t('dndDiceHint')
    : ''
)

const skills = ref<CharacterSkill[]>([])
const background = ref('')
const gold = ref(0)
const equipment = ref<CharacterItem[]>([])
const inventory = ref<CharacterItem[]>([])
const pool = computed(() => props.skillPool || [])
const skillHint = computed(() => localizedField<string>(props.ruleMeta, 'skill_hint') || '')
const maxSkills = computed(() => Number(props.ruleMeta?.max_skills || 0))
const skillPointTotal = computed(() => Number(props.ruleMeta?.skill_point_total || 0))
const maxSkillValue = computed(() => Number(props.ruleMeta?.max_skill_value || 0))
const skillSpent = computed(() => skills.value.reduce((sum, skill) => sum + skillPointCost(skill, props.ruleMeta), 0))
const skillOverLimit = computed(() =>
  Boolean((maxSkills.value && skills.value.filter(s => s.name?.trim()).length > maxSkills.value)
    || (skillPointTotal.value && skillSpent.value > skillPointTotal.value)
    || (maxSkillValue.value && skills.value.some(s => (Number(s.value || 0) || 0) > maxSkillValue.value)))
)

const autoHp = computed(() => isAutoHpRule(props.ruleMeta))
const autoHpValue = computed(() => calcAutoHp(attrs.value, props.ruleMeta))

watch(
  () => props.ruleAttrs,
  (ras) => {
    const suggested = suggestedAttributes(ras, props.attrTotal)
    for (const a of ras) {
      if (attrs.value[a.key] === undefined) attrs.value[a.key] = suggested[a.key] ?? Math.floor((a.min + a.max) / 2)
    }
  },
  { immediate: true }
)

watch(
  () => props.initial,
  (c) => { if (c) { resetFields(); applyCharacter(c) } },
  { immediate: true }
)

function resetFields() {
  characterName.value = ''
  identityValues.value = {}
  skills.value = []
  background.value = ''
  gold.value = 0
  equipment.value = []
  inventory.value = []
  step.value = 1
  attrs.value = suggestedAttributes(props.ruleAttrs, props.attrTotal)
}

function fillSuggested() {
  attrs.value = suggestedAttributes(props.ruleAttrs, props.attrTotal)
}
function resetAttrs() {
  const a: Record<string, number> = {}
  for (const r of props.ruleAttrs) a[r.key] = r.min
  attrs.value = a
}

async function generateByAI() {
  if (!aiPrompt.value.trim()) { toast.error(t('enterCharacterPrompt')); return }
  generating.value = true
  try {
    const body: Record<string, unknown> = { prompt: aiPrompt.value }
    if (props.gameKey) body.game_key = props.gameKey
    else if (props.ruleId) body.rule_id = props.ruleId
    body.language = props.language || locale.value
    const r = await api<GeneratedCharacterResponse>('/generate-character', { method: 'POST', body: JSON.stringify(body) })
    if (!r.ok || !r.character) throw new Error(r.error || t('generationFailed'))
    applyCharacter(r.character)
    toast.success(t('aiGeneratedCharacterToast'))
  } catch (e: unknown) { toast.error(errorMessage(e)) } finally { generating.value = false }
}

function skillToDraft(skill: string | CharacterSkill): CharacterSkill {
  return typeof skill === 'string' ? { name: skill, value: 20 } : { name: skill.name || '', value: skill.value || 20 }
}

function applyCharacter(c: CharacterSheet) {
  const record = c as Record<string, unknown>
  if (c.character_name) characterName.value = c.character_name
  const fields = identitySchema(props.ruleMeta)
  for (const f of fields) {
    const v = c.identity?.[f.key] ?? (f.legacy_field ? record[f.legacy_field] : '') ?? ''
    if (v) identityValues.value[f.key] = String(v)
  }
  if (c.attributes) {
    for (const [k, v] of Object.entries(c.attributes)) attrs.value[k] = Number(v) || 0
  }
  if (Array.isArray(c.skills) && c.skills.length) skills.value = c.skills.map(skillToDraft)
  if (Array.isArray(c.equipment)) equipment.value = c.equipment.map(it => ({ ...it }))
  if (Array.isArray(c.inventory)) inventory.value = c.inventory.map(it => ({ ...it }))
  if (c.background) background.value = c.background
  if (c.currency?.amount !== undefined) gold.value = Number(c.currency.amount) || 0
  else if (c.gold !== undefined) gold.value = Number(c.gold) || 0
}

function canNext() {
  if (step.value === 1) return characterName.value.trim().length > 0
  return true
}
function next() { if (canNext() && step.value < 4) step.value = (step.value + 1) as 1 | 2 | 3 | 4 }
function prev() { if (step.value > 1) step.value = (step.value - 1) as 1 | 2 | 3 | 4 }

function finish() {
  const fields = identitySchema(props.ruleMeta)
  const updates: SheetUpdate = {}
  for (const f of fields) setIdentityUpdate(updates, f, identityValues.value[f.key] || '')
  const identity = updates.identity || {}
  identity.background = background.value

  const character: CharacterSubmit = {
    character_name: characterName.value.trim(),
    identity,
    attributes: { ...attrs.value },
    skills: skills.value.filter(s => s.name?.trim()).map(s => ({ name: s.name.trim(), value: Number(s.value) || 0 })),
    equipment: equipment.value.filter(it => String(it.name || '').trim()).map(it => ({ name: String(it.name).trim(), type: it.type || 'weapon', damage: Number(it.damage) || 0, slot: it.slot || 'main_hand', quality: it.quality || 'common' })),
    inventory: inventory.value.filter(it => String(it.name || '').trim()).map(it => ({ name: String(it.name).trim(), qty: Number(it.qty) || 1, effect: it.effect || '' })),
    background: background.value,
    gold: gold.value,
    currency: { amount: gold.value },
  }
  Object.assign(character, updates)
  emit('submit', character)
}
</script>

<template>
  <div class="wizard">
    <section class="wizard-inner">
      <div class="wizard-steps">
      <span v-for="n in 4" :key="n" :class="['wizard-step', { active: step === n, done: step > n }]">
        {{ n === 1 ? t('identityStep') : n === 2 ? t('attributes') : n === 3 ? t('skillsBackgroundStep') : t('equipment') }}
      </span>
    </div>

    <div v-if="step === 1" class="wizard-pane">
      <label>{{ t('characterName') }}<input v-model="characterName" :placeholder="t('nameCharacterPlaceholder')"></label>
      <label v-for="f in identityFields" :key="f.key">{{ identityLabel(f) }}<input v-model="identityValues[f.key]"></label>

      <details class="ai-block">
        <summary>{{ t('aiGenerateCharacter') }}</summary>
        <textarea v-model="aiPrompt" rows="3" :placeholder="t('aiCharacterPromptPlaceholder')"></textarea>
        <button class="primary" :disabled="generating" @click="generateByAI">{{ generating ? t('generatingEllipsis') : t('aiGenerate') }}</button>
      </details>
    </div>

    <div v-else-if="step === 2" class="wizard-pane">
      <p v-if="attrHint" class="form-hint">{{ attrHint }}</p>
      <p v-if="diceHint" class="form-hint">{{ diceHint }}</p>
      <p class="attr-points" :class="{ over: overLimit }">
        {{ t('attrTotalRemaining', { sum: attrSum, total: attrTotal, remaining: attrPoints }) }}
        <span v-if="overLimit" class="warn">{{ t('overLimit') }}</span>
      </p>
      <div class="attr-actions">
        <button @click="fillSuggested">{{ t('fillSuggestedValues') }}</button>
        <button @click="resetAttrs">{{ t('reset') }}</button>
      </div>
      <div class="attr-sliders">
        <div v-for="a in ruleAttrs" :key="a.key" class="attr-row">
          <span class="attr-name">{{ attrDisplayName(a) }}</span>
          <input type="range" :min="a.min" :max="a.max * 2" v-model.number="attrs[a.key]">
          <input type="number" class="attr-val" :min="a.min" v-model.number="attrs[a.key]">
        </div>
      </div>
      <p v-if="autoHp && autoHpValue" class="form-hint">{{ t('ruleSuggestedHp') }}: <strong>{{ autoHpValue }}</strong></p>
    </div>

    <div v-else-if="step === 3" class="wizard-pane">
      <label>{{ t('skills') }}</label>
      <p v-if="skillHint" class="form-hint">{{ skillHint }}</p>
      <p class="form-hint" :class="{ warn: skillOverLimit }">
        <span v-if="maxSkills">{{ t('skillCount', { count: skills.filter(s => s.name?.trim()).length, max: maxSkills }) }}</span>
        <span v-if="skillPointTotal"> · {{ t('skillPointsSpent', { spent: skillSpent, total: skillPointTotal }) }}</span>
        <span v-if="maxSkillValue"> · {{ t('maxSingleSkill', { max: maxSkillValue }) }}</span>
      </p>
      <SkillEditor v-model="skills" :pool="pool" />
      <label>{{ t('backgroundStory') }}<textarea v-model="background" rows="4" :placeholder="t('backgroundPlaceholder')"></textarea></label>
      <label>{{ currencyLabel(ruleMeta) }}<input type="number" v-model.number="gold" min="0"></label>
    </div>

    <div v-else-if="step === 4" class="wizard-pane">
      <p class="form-hint">{{ t('itemsStepHint') }}</p>
      <ItemEditor v-model:equipment="equipment" v-model:inventory="inventory" />
    </div>

    <div class="wizard-actions">
      <button @click="emit('cancel')">{{ t('cancel') }}</button>
      <button v-if="step > 1" @click="prev">{{ t('previous') }}</button>
      <button v-if="step < 4" class="primary" :disabled="!canNext()" @click="next">{{ t('next') }}</button>
      <button v-else class="primary" @click="finish">{{ t('finish') }}</button>
    </div>
  </section>
  </div>
</template>
