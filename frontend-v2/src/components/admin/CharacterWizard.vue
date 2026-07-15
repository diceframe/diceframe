<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorMessage } from '../../api/client'
import type { CharacterSheet, CharacterSkill, GeneratedCharacterResponse, RuleMeta, SkillSpec } from '../../api/types'
import { useToast } from '../../composables/useToast'
import SkillEditor from './SkillEditor.vue'
import {
  identitySchema, identityLabel, attrDisplayName, currencyLabel,
  isAutoHpRule, calcAutoHp, setIdentityUpdate, suggestedAttributes, skillPointCost,
  type IdentityField, type RuleAttr,
} from '../../utils/ruleSchema'

interface CharacterSubmit extends CharacterSheet { character_name: string }

type SheetUpdate = Partial<CharacterSheet> & { identity?: Record<string, string> }

const props = defineProps<{
  ruleMeta?: RuleMeta | null
  ruleAttrs: RuleAttr[]
  attrTotal: number
  skillPool?: Array<string | SkillSpec>
  ruleId?: string
  gameKey?: string
  initial?: CharacterSheet
}>()
const emit = defineEmits<{ submit: [character: CharacterSubmit]; cancel: [] }>()

const toast = useToast()
const step = ref<1 | 2 | 3>(1)

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
const attrHint = computed(() => props.ruleMeta?.attr_hint || '')
const diceHint = computed(() =>
  props.ruleMeta?.mechanics === 'dnd5e_core'
    ? 'DND 小抄：优势=2d20取高，劣势=2d20取低；同时有优势和劣势时抵消。'
    : ''
)

const skills = ref<CharacterSkill[]>([])
const background = ref('')
const gold = ref(0)
const pool = computed(() => props.skillPool || [])
const skillHint = computed(() => props.ruleMeta?.skill_hint || '')
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
  if (!aiPrompt.value.trim()) { toast.error('请输入角色描述'); return }
  generating.value = true
  try {
    const body: Record<string, unknown> = { prompt: aiPrompt.value }
    if (props.gameKey) body.game_key = props.gameKey
    else if (props.ruleId) body.rule_id = props.ruleId
    const r = await api<GeneratedCharacterResponse>('/generate-character', { method: 'POST', body: JSON.stringify(body) })
    if (!r.ok || !r.character) throw new Error(r.error || '生成失败')
    applyCharacter(r.character)
    toast.success('AI 已生成角色，可在各步继续调整')
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
  if (c.background) background.value = c.background
  if (c.currency?.amount !== undefined) gold.value = Number(c.currency.amount) || 0
  else if (c.gold !== undefined) gold.value = Number(c.gold) || 0
}

function canNext() {
  if (step.value === 1) return characterName.value.trim().length > 0
  return true
}
function next() { if (canNext() && step.value < 3) step.value = (step.value + 1) as 1 | 2 | 3 }
function prev() { if (step.value > 1) step.value = (step.value - 1) as 1 | 2 | 3 }

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
      <span v-for="n in 3" :key="n" :class="['wizard-step', { active: step === n, done: step > n }]">
        {{ n === 1 ? '身份' : n === 2 ? '属性' : '技能与背景' }}
      </span>
    </div>

    <div v-if="step === 1" class="wizard-pane">
      <label>角色名<input v-model="characterName" placeholder="为角色命名"></label>
      <label v-for="f in identityFields" :key="f.key">{{ identityLabel(f) }}<input v-model="identityValues[f.key]"></label>

      <details class="ai-block">
        <summary>AI 生成角色</summary>
        <textarea v-model="aiPrompt" rows="3" placeholder="用自然语言描述角色：身份、性格、经历，AI 自动填充全部字段"></textarea>
        <button class="primary" :disabled="generating" @click="generateByAI">{{ generating ? '生成中…' : 'AI 生成' }}</button>
      </details>
    </div>

    <div v-else-if="step === 2" class="wizard-pane">
      <p v-if="attrHint" class="form-hint">{{ attrHint }}</p>
      <p v-if="diceHint" class="form-hint">{{ diceHint }}</p>
      <p class="attr-points" :class="{ over: overLimit }">
        总和 {{ attrSum }} / {{ attrTotal }} · 剩余 {{ attrPoints }} 点
        <span v-if="overLimit" class="warn">已超限</span>
      </p>
      <div class="attr-actions">
        <button @click="fillSuggested">填建议值</button>
        <button @click="resetAttrs">重置</button>
      </div>
      <div class="attr-sliders">
        <div v-for="a in ruleAttrs" :key="a.key" class="attr-row">
          <span class="attr-name">{{ attrDisplayName(a) }}</span>
          <input type="range" :min="a.min" :max="a.max * 2" v-model.number="attrs[a.key]">
          <input type="number" class="attr-val" :min="a.min" v-model.number="attrs[a.key]">
        </div>
      </div>
      <p v-if="autoHp && autoHpValue" class="form-hint">规则建议 HP：<strong>{{ autoHpValue }}</strong></p>
    </div>

    <div v-else class="wizard-pane">
      <label>技能</label>
      <p v-if="skillHint" class="form-hint">{{ skillHint }}</p>
      <p class="form-hint" :class="{ warn: skillOverLimit }">
        <span v-if="maxSkills">技能 {{ skills.filter(s => s.name?.trim()).length }} / {{ maxSkills }}</span>
        <span v-if="skillPointTotal"> · 技能点 {{ skillSpent }} / {{ skillPointTotal }}</span>
        <span v-if="maxSkillValue"> · 单技能上限 {{ maxSkillValue }}</span>
      </p>
      <SkillEditor v-model="skills" :pool="pool" />
      <label>背景故事<textarea v-model="background" rows="4" placeholder="角色经历、动机、人际关系"></textarea></label>
      <label>{{ currencyLabel(ruleMeta) }}<input type="number" v-model.number="gold" min="0"></label>
    </div>

    <div class="wizard-actions">
      <button @click="emit('cancel')">取消</button>
      <button v-if="step > 1" @click="prev">上一步</button>
      <button v-if="step < 3" class="primary" :disabled="!canNext()" @click="next">下一步</button>
      <button v-else class="primary" @click="finish">完成</button>
    </div>
  </section>
  </div>
</template>
