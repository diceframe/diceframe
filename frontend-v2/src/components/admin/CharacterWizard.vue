<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorMessage } from '@/api/client'
import type { CharacterSheet, CharacterSkill, CharacterItem, CharacterPortrait, GeneratedCharacterResponse, RuleMeta, SkillSpec } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useLocale } from '@/composables/useLocale'
import SkillEditor from './SkillEditor.vue'
import ItemEditor from './ItemEditor.vue'
import PortraitPicker from './PortraitPicker.vue'
import {
  identitySchema, identityLabel, attrDisplayName, currencyLabel,
  isAutoHpRule, calcAutoHp, setIdentityUpdate, suggestedAttributes, skillPointCost, skillPointBudget, localizedField,
  type IdentityField, type RuleAttr,
} from '@/utils/ruleSchema'
import type { CurrencySystem } from '@/utils/currency'
import { currencyAmountToInputText, currencyEditableUnitLabel, currencyInputStep, parseCurrencyInput } from '@/utils/currency'

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
const portrait = ref<CharacterPortrait | null>()
const identityValues = ref<Record<string, string>>({})
const ruleClasses = computed<Array<{ id?: string; name?: string }>>(() => {
  const raw = (props.ruleMeta as Record<string, unknown> | null | undefined)?.classes
  return Array.isArray(raw) ? raw as Array<{ id?: string; name?: string }> : []
})
const classOptions = computed(() => ruleClasses.value.map(c => String(c.name || '')).filter(Boolean))
const hasClassPicker = computed(() => classOptions.value.length > 0)
const classLevelTotal = computed(() => {
  const raw = Number((props.ruleMeta as Record<string, unknown> | null | undefined)?.class_levels_total ?? 0)
  return Number.isFinite(raw) && raw > 0 ? raw : 5
})
const primaryClass = ref('')
const secondaryClass = ref('')
const primaryLevel = ref(1)
const secondaryLevel = ref(1)
const classLevelSum = computed(() => Number(primaryLevel.value || 0) + Number(secondaryLevel.value || 0))
const classSelectionOk = computed(() => {
  if (!hasClassPicker.value) return true
  if (!primaryClass.value || !secondaryClass.value) return false
  if (primaryClass.value === secondaryClass.value) return false
  const a = Number(primaryLevel.value) || 0
  const b = Number(secondaryLevel.value) || 0
  return a >= 1 && b >= 1 && a + b === classLevelTotal.value
})
const classSkillPool = computed<string[]>(() => {
  if (!hasClassPicker.value) return []
  const pools = (props.ruleMeta as Record<string, unknown> | null | undefined)?.skill_pools
  const skills = (props.ruleMeta as Record<string, unknown> | null | undefined)?.skills
  if (!pools || typeof pools !== 'object' || !skills || typeof skills !== 'object') return []
  const names: string[] = []
  for (const className of [primaryClass.value, secondaryClass.value]) {
    const cls = ruleClasses.value.find(c => String(c.name || '') === className)
    if (!cls?.id) continue
    for (const skillId of (pools as Record<string, string[]>)[String(cls.id)] || []) {
      const name = String(((skills as Record<string, { name?: string }>)[skillId] || {}).name || '')
      if (name && !names.includes(name)) names.push(name)
    }
  }
  return names
})
const identityFields = computed<IdentityField[]>(() =>
  identitySchema(props.ruleMeta).filter(
    f => f.key !== 'background' && !(hasClassPicker.value && f.key === 'archetype'),
  )
)

watch(
  [classOptions, classLevelTotal],
  () => {
    if (!hasClassPicker.value) return
    if (!classOptions.value.includes(primaryClass.value)) primaryClass.value = classOptions.value[0] || ''
    if (!classOptions.value.includes(secondaryClass.value) || secondaryClass.value === primaryClass.value) {
      secondaryClass.value = classOptions.value.find(name => name !== primaryClass.value) || ''
    }
    const total = classLevelTotal.value
    if (Number(primaryLevel.value) < 1) primaryLevel.value = Math.max(1, total - 1)
    if (Number(secondaryLevel.value) < 1) secondaryLevel.value = Math.max(1, total - 1)
    const sum = Number(primaryLevel.value) + Number(secondaryLevel.value)
    if (sum !== total) {
      primaryLevel.value = Math.max(1, total - 1)
      secondaryLevel.value = Math.max(1, total - Number(primaryLevel.value))
    }
  },
  { immediate: true },
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
const damageDiceEnabled = computed(() => props.ruleMeta?.mechanics === 'dnd5e_core')

const skills = ref<CharacterSkill[]>([])
const background = ref('')
// canonical 金额内部保存；输入框按展示单位编辑（0.25），提交前经 parser 换算。
const gold = ref(0)
const goldInput = ref('0')
const startingCurrency = computed(() => {
  const raw = Number((props.ruleMeta as Record<string, unknown> | null | undefined)?.starting_currency ?? 0)
  return Number.isFinite(raw) && raw > 0 ? raw : 0
})
// 规则声明了初始资金时自动填入；用户改过就不再覆盖。
watch(
  startingCurrency,
  (amount) => {
    if (!amount) return
    if (gold.value !== 0 || goldInput.value !== '0') return
    gold.value = amount
    goldInput.value = currencyAmountToInputText(amount, currencySystem.value)
  },
  { immediate: true },
)
const currencySystem = computed<CurrencySystem | null>(() => props.ruleMeta?.currency_system || null)
const goldInvalid = computed(() => parseCurrencyInput(goldInput.value, currencySystem.value, { allowZero: true }) === null)
const equipment = ref<CharacterItem[]>([])
const inventory = ref<CharacterItem[]>([])
const pool = computed(() => props.skillPool || [])
const skillHint = computed(() => localizedField<string>(props.ruleMeta, 'skill_hint') || '')
const maxSkills = computed(() => Number(props.ruleMeta?.max_skills || 0))
const skillPointTotal = computed(() => skillPointBudget(props.ruleMeta, attrs.value))
const maxSkillValue = computed(() => Number(props.ruleMeta?.max_skill_value || 0))
const skillSpent = computed(() => skills.value.reduce((sum, skill) => sum + skillPointCost(skill, props.ruleMeta, attrs.value), 0))
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
  portrait.value = null
  identityValues.value = {}
  skills.value = []
  background.value = ''
  gold.value = startingCurrency.value
  goldInput.value = currencyAmountToInputText(startingCurrency.value, currencySystem.value)
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
  if (typeof skill === 'string') return { name: skill, value: 20 }
  const row: CharacterSkill = { name: skill.name || '', value: skill.value || 20 }
  const effect = String(skill.effect || '').trim()
  if (effect) row.effect = effect
  return row
}

/** 提交侧技能投影；effect 只做长度收口，不改机械字段。 */
function skillToPayload(skill: CharacterSkill): CharacterSkill {
  const row: CharacterSkill = { name: String(skill.name).trim(), value: Number(skill.value) || 0 }
  const effect = String(skill.effect || '').trim().slice(0, 500)
  if (effect) row.effect = effect
  return row
}

function applyCharacter(c: CharacterSheet) {
  const record = c as Record<string, unknown>
  if (c.character_name) characterName.value = c.character_name
  portrait.value = c.portrait ? { ...c.portrait } : undefined
  const fields = identitySchema(props.ruleMeta)
  for (const f of fields) {
    const v = c.identity?.[f.key] ?? (f.legacy_field ? record[f.legacy_field] : '') ?? ''
    if (v) identityValues.value[f.key] = String(v)
  }
  if (c.attributes) {
    const targetKeys = new Set(props.ruleAttrs.map(attr => attr.key))
    for (const [k, v] of Object.entries(c.attributes)) {
      if (!targetKeys.size || targetKeys.has(k)) attrs.value[k] = Number(v) || 0
    }
  }
  if (Array.isArray(c.skills) && c.skills.length) skills.value = c.skills.map(skillToDraft)
  if (Array.isArray(c.equipment)) equipment.value = c.equipment.map(it => ({ ...it }))
  if (Array.isArray(c.inventory)) inventory.value = c.inventory.map(it => ({ ...it }))
  if (c.background) background.value = c.background
  const storedLevels = (c as Record<string, unknown>).class_levels
  if (storedLevels && typeof storedLevels === 'object') {
    const entries = Object.entries(storedLevels as Record<string, unknown>)
      .map(([name, level]) => [name, Number(level) || 0] as const)
      .filter(([, level]) => level > 0)
    if (entries.length >= 2) {
      primaryClass.value = entries[0][0]
      primaryLevel.value = entries[0][1]
      secondaryClass.value = entries[1][0]
      secondaryLevel.value = entries[1][1]
    }
  }
  if (c.currency?.amount !== undefined) gold.value = Number(c.currency.amount) || 0
  else if (c.gold !== undefined) gold.value = Number(c.gold) || 0
  goldInput.value = currencyAmountToInputText(gold.value, currencySystem.value)
}

watch(goldInput, (text) => {
  const parsed = parseCurrencyInput(text, currencySystem.value, { allowZero: true })
  if (parsed !== null) gold.value = parsed
})

function canNext() {
  if (step.value === 1) return characterName.value.trim().length > 0 && classSelectionOk.value
  return true
}
function next() { if (canNext() && step.value < 4) step.value = (step.value + 1) as 1 | 2 | 3 | 4 }
function prev() { if (step.value > 1) step.value = (step.value - 1) as 1 | 2 | 3 | 4 }

function finish() {
  if (!classSelectionOk.value) { toast.error(t('classLevelsInvalid')); return }
  const parsedGold = parseCurrencyInput(goldInput.value, currencySystem.value, { allowZero: true })
  if (parsedGold === null) { toast.error(t('invalidAmount')); return }
  gold.value = parsedGold

  const fields = identitySchema(props.ruleMeta)
  const updates: SheetUpdate = {}
  for (const f of fields) setIdentityUpdate(updates, f, identityValues.value[f.key] || '')
  const identity = updates.identity || {}
  identity.background = background.value
  if (hasClassPicker.value) {
    // FU：职业是"两个职业 + 5 级分配"，存成可读字符串，同时保留结构化 class_levels。
    const archetype = `${primaryClass.value} ${Number(primaryLevel.value)} / ${secondaryClass.value} ${Number(secondaryLevel.value)}`
    identity.archetype = archetype
    updates.class = archetype
  }

  const character: CharacterSubmit = {
    character_name: characterName.value.trim(),
    portrait: portrait.value ? { ...portrait.value } : null,
    identity,
    attributes: { ...attrs.value },
    skills: skills.value.filter(s => s.name?.trim()).map(skillToPayload),
    equipment: equipment.value.filter(it => String(it.name || '').trim()).map(it => ({
      name: String(it.name).trim(), type: it.type || 'weapon', damage: Number(it.damage) || 0,
      ...(damageDiceEnabled.value && String(it.damage_dice || '').trim() ? { damage_dice: String(it.damage_dice).trim() } : {}),
      slot: it.slot || 'main_hand', quality: it.quality || 'common',
    })),
    inventory: inventory.value.filter(it => String(it.name || '').trim()).map(it => ({ name: String(it.name).trim(), qty: Number(it.qty) || 1, effect: it.effect || '' })),
    background: background.value,
    gold: gold.value,
    currency: { amount: gold.value },
  }
  Object.assign(character, updates)
  if (hasClassPicker.value) {
    ;(character as Record<string, unknown>).class_levels = {
      [primaryClass.value]: Number(primaryLevel.value),
      [secondaryClass.value]: Number(secondaryLevel.value),
    }
  }
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
      <PortraitPicker v-model="portrait" :rule-id="ruleId" :seed="characterName" :name="characterName" />
      <div v-if="hasClassPicker" class="class-picker">
        <label>{{ t('classRole') }} 1
          <select v-model="primaryClass">
            <option v-for="name in classOptions" :key="'pc-' + name" :value="name">{{ name }}</option>
          </select>
        </label>
        <label>{{ t('classLevel') }}<input type="number" min="1" :max="classLevelTotal - 1" v-model.number="primaryLevel"></label>
        <label>{{ t('classRole') }} 2
          <select v-model="secondaryClass">
            <option v-for="name in classOptions" :key="'sc-' + name" :value="name" :disabled="name === primaryClass">{{ name }}</option>
          </select>
        </label>
        <label>{{ t('classLevel') }}<input type="number" min="1" :max="classLevelTotal - 1" v-model.number="secondaryLevel"></label>
        <p class="form-hint" :class="{ warn: !classSelectionOk }">
          {{ t('classLevelsHint', { sum: classLevelSum, total: classLevelTotal }) }}
        </p>
      </div>
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
          <input type="range" :min="a.min" :max="a.max" v-model.number="attrs[a.key]">
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
      <SkillEditor v-model="skills" :pool="hasClassPicker && classSkillPool.length ? classSkillPool : pool" :meta="ruleMeta" />
      <label>{{ t('backgroundStory') }}<textarea v-model="background" rows="4" :placeholder="t('backgroundPlaceholder')"></textarea></label>
      <label>{{ currencyEditableUnitLabel(currencySystem, currencyLabel(ruleMeta)) }}<input type="text" inputmode="decimal" v-model="goldInput" :step="currencyInputStep(currencySystem)" :class="{ invalid: goldInvalid }"></label>
    </div>

    <div v-else-if="step === 4" class="wizard-pane">
      <p class="form-hint">{{ t('itemsStepHint') }}</p>
      <ItemEditor v-model:equipment="equipment" v-model:inventory="inventory" :damage-dice-enabled="damageDiceEnabled" />
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
