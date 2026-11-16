<script setup lang="ts">
/**
 * 规则驱动的专业建卡器。
 *
 * 屏幕上的每一个控件都来自运行时自己回答的 builder_choices：属性枚举、骰面
 * 档位、属性总和、职业清单、技能点数预算。校验与最终角色卡也交给运行时
 * （validate / finalize），前端只负责摆放和不让人白填。
 *
 * 这样一来，任何实现 builder_choices 的规则运行时都能建卡，不需要平台为它
 * 单独写一个 Vue 组件——专门的建卡器（如 dnd2024）仍然优先，这里只是缺省实现。
 */
import { computed, onMounted, ref } from 'vue'
import type { CharacterSheet, JsonObject } from '@/api/types'
import {
  fetchRulesetBuilderChoices,
  finalizeRulesetBuilderCharacter,
  validateRulesetBuilderDraft,
} from '@/api/rulesets'
import { api } from '@/api/client'

const props = withDefaults(defineProps<{
  ruleId: string
  language?: string
  initial?: CharacterSheet
  embedded?: boolean
}>(), { language: 'zh-CN', embedded: false })
const emit = defineEmits<{
  submit: [character: CharacterSheet, options?: { targetLevel?: number }]
  cancel: []
}>()

const zh = computed(() => props.language.toLowerCase().startsWith('zh'))
function text(zhText: string, enText: string): string {
  return zh.value ? zhText : enText
}

interface AttributeSpec { key: string; name: string; options: number[] }
interface SkillSpec { name: string; value: number }
interface Choices {
  attributes: AttributeSpec[]
  attribute_total: number
  classes: Array<string | { name?: string; id?: string }>
  class_levels_total: number
  skills?: { total?: number; max_per_skill?: number; max_count?: number }
  start_level?: number
}

const loading = ref(true)
const busy = ref(false)
const error = ref('')
const choices = ref<Choices | null>(null)
const name = ref('')
const attrs = ref<Record<string, number>>({})
const classLevels = ref<Record<string, number>>({})
const skills = ref<SkillSpec[]>([])
const skillNames = ref<string[]>([])
const extraClass = ref('')

const classNames = computed<string[]>(() => (choices.value?.classes || []).map(entry => (
  typeof entry === 'string' ? entry : String(entry?.name || entry?.id || '')
)).filter(Boolean))
const attrTotal = computed(() => Object.values(attrs.value).reduce((sum, value) => sum + Number(value || 0), 0))
const attrBudget = computed(() => Number(choices.value?.attribute_total || 0))
const attrLeft = computed(() => (attrBudget.value ? attrBudget.value - attrTotal.value : 0))
const classTotal = computed(() => Object.values(classLevels.value).reduce((sum, value) => sum + Number(value || 0), 0))
const classBudget = computed(() => Number(choices.value?.class_levels_total || 0))
const skillPoints = computed(() => skills.value.reduce((sum, row) => sum + Number(row.value || 0), 0))
const skillMax = computed(() => Number(choices.value?.skills?.max_per_skill || 0))
const skillCountMax = computed(() => Number(choices.value?.skills?.max_count || 0))
const skillBudget = computed(() => Number(choices.value?.skills?.total || 0))
const availableSkills = computed(() => skillNames.value.filter(item => !skills.value.some(row => row.name === item)))

function draft(): JsonObject {
  return {
    character_name: name.value.trim(),
    attributes: { ...attrs.value },
    class_levels: { ...classLevels.value },
    skills: skills.value
      .filter(row => row.name.trim())
      .map(row => ({ name: row.name.trim(), value: Number(row.value) || 1 })),
  }
}

/**
 * 默认属性分配：先全部给最低档，再按「谁的当前值最小」轮流加一档，
 * 直到用完总和。对 d6/d8/d10/d12 + 总和 32 就是 8/8/8/8——合法且均衡，
 * 玩家自己再调。
 */
function suggestAttributes(specs: AttributeSpec[], total: number): Record<string, number> {
  const result: Record<string, number> = {}
  const ladders: Record<string, number[]> = {}
  for (const spec of specs) {
    const options = [...spec.options].sort((a, b) => a - b)
    ladders[spec.key] = options
    result[spec.key] = options[0] ?? 0
  }
  if (!total) return result
  let left = total - Object.values(result).reduce((sum, value) => sum + value, 0)
  for (let guard = 0; left > 0 && guard < 500; guard += 1) {
    const candidates = [...specs].sort((a, b) => result[a.key] - result[b.key])
    let stepped = false
    for (const spec of candidates) {
      const ladder = ladders[spec.key] || []
      const next = ladder.find(value => value > result[spec.key])
      if (next === undefined) continue
      const step = next - result[spec.key]
      if (step > left) continue
      result[spec.key] = next
      left -= step
      stepped = true
      break
    }
    if (!stepped) break
  }
  return result
}

function blankDraft(specs: AttributeSpec[], total: number): void {
  name.value = ''
  attrs.value = suggestAttributes(specs, total)
  classLevels.value = {}
  if (classNames.value.length) {
    classLevels.value = { [classNames.value[0]]: Number(choices.value?.class_levels_total || 1) }
  }
  skills.value = []
  extraClass.value = ''
}

function draftFromInitial(): boolean {
  const canonical = (props.initial?.ruleset_character || {}) as Record<string, unknown>
  if (!canonical || !Object.keys(canonical).length) return false
  name.value = String(props.initial?.character_name || '')
  const rawAttrs = canonical.attributes
  if (rawAttrs && typeof rawAttrs === 'object') {
    const next: Record<string, number> = {}
    for (const [key, value] of Object.entries(rawAttrs as Record<string, unknown>)) {
      const numeric = Number(value)
      if (Number.isFinite(numeric)) next[key] = numeric
    }
    attrs.value = { ...attrs.value, ...next }
  }
  const rawLevels = canonical.class_levels
  if (rawLevels && typeof rawLevels === 'object') {
    const next: Record<string, number> = {}
    for (const [key, value] of Object.entries(rawLevels as Record<string, unknown>)) {
      const numeric = Number(value)
      if (Number.isFinite(numeric)) next[key] = numeric
    }
    classLevels.value = next
  }
  if (Array.isArray(canonical.skills)) {
    skills.value = canonical.skills
      .filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object')
      .map(row => ({ name: String(row.name || ''), value: Number(row.value) || 1 }))
      .filter(row => row.name)
  }
  return true
}

/** 技能名只做提示：规则自己的技能表里取名字，取不到就纯手填。 */
async function loadSkillNames(): Promise<void> {
  try {
    const detail = await api<{ rule?: Record<string, unknown> }>(
      `/rules/${encodeURIComponent(props.ruleId)}?language=${encodeURIComponent(props.language)}`,
    )
    const table = (detail?.rule || {}).skills
    if (!table || typeof table !== 'object') return
    skillNames.value = Array.from(new Set(
      Object.values(table as Record<string, unknown>)
        .map(row => (row && typeof row === 'object' ? String((row as Record<string, unknown>).name || '') : ''))
        .filter(Boolean),
    )).sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'))
  } catch {
    skillNames.value = []
  }
}

function addClass(): void {
  const chosen = extraClass.value
  if (!chosen || classLevels.value[chosen]) return
  classLevels.value = { ...classLevels.value, [chosen]: 0 }
  extraClass.value = ''
}

function dropClass(key: string): void {
  const next = { ...classLevels.value }
  delete next[key]
  classLevels.value = next
}

function addSkill(): void {
  if (skillCountMax.value && skills.value.length >= skillCountMax.value) return
  skills.value = [...skills.value, { name: '', value: 1 }]
}

function dropSkill(index: number): void {
  skills.value = skills.value.filter((_, idx) => idx !== index)
}

async function submit(): Promise<void> {
  if (busy.value) return
  if (!name.value.trim()) {
    error.value = text('请先给角色起个名字。', 'Give the character a name first.')
    return
  }
  busy.value = true
  error.value = ''
  try {
    const payload = draft()
    const validation = await validateRulesetBuilderDraft(props.ruleId, payload, props.language)
    if (!validation.valid) {
      error.value = validation.errors.join('；') || text('这张卡还没通过规则校验。', 'This card does not pass the ruleset yet.')
      return
    }
    const result = await finalizeRulesetBuilderCharacter(props.ruleId, payload, props.language)
    const character = { ...(result.character as unknown as CharacterSheet) }
    character.character_name = name.value.trim()
    emit('submit', character as CharacterSheet)
  } catch (cause) {
    error.value = String((cause as Error)?.message || cause)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    const response = await fetchRulesetBuilderChoices(props.ruleId, {}, props.language)
    const raw = (response.choices || {}) as unknown as Choices
    choices.value = {
      ...raw,
      attributes: Array.isArray(raw.attributes) ? raw.attributes : [],
      attribute_total: Number(raw.attribute_total || 0),
      classes: Array.isArray(raw.classes) ? raw.classes : [],
      class_levels_total: Number(raw.class_levels_total || 0),
    }
    attrs.value = suggestAttributes(choices.value.attributes, choices.value.attribute_total)
    if (!draftFromInitial()) {
      blankDraft(choices.value.attributes, choices.value.attribute_total)
    } else if (classNames.value.length && !Object.keys(classLevels.value).length) {
      classLevels.value = { [classNames.value[0]]: Number(choices.value.class_levels_total || 1) }
    }
    if (choices.value.attributes.length) void loadSkillNames()
  } catch (cause) {
    error.value = String((cause as Error)?.message || cause)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section :class="['generic-ruleset-builder', { embedded }]" aria-labelledby="generic-builder-title">
    <header class="head">
      <div>
        <span class="eyebrow">{{ text('规则驱动 · 专业建卡', 'Ruleset-driven builder') }}</span>
        <h2 id="generic-builder-title">{{ text('创建角色', 'Create a character') }}</h2>
        <p>{{ text('属性、职业与技能都由当前规则校验，不合法会在这里直接告诉你。', 'Attributes, classes and skills are checked by the ruleset; problems show up here.') }}</p>
      </div>
      <button type="button" class="close" :aria-label="text('关闭', 'Close')" @click="emit('cancel')">×</button>
    </header>

    <div v-if="loading" class="state" role="status">{{ text('正在读取规则数据…', 'Loading ruleset data…') }}</div>

    <template v-else-if="choices">
      <label class="field">
        <span>{{ text('名字', 'Name') }}</span>
        <input v-model="name" type="text" maxlength="40" :placeholder="text('角色名', 'Character name')" />
      </label>

      <section v-if="choices.attributes.length" class="block">
        <div class="block-head">
          <h3>{{ text('属性', 'Attributes') }}</h3>
          <span :class="['budget', { over: attrLeft < 0 }]">
            {{ text('总和', 'Total') }} {{ attrTotal }} / {{ attrBudget || '—' }}
          </span>
        </div>
        <div class="attrs">
          <label v-for="spec in choices.attributes" :key="spec.key" class="attr">
            <span>{{ spec.name }}</span>
            <select v-model.number="attrs[spec.key]">
              <option v-for="option in spec.options" :key="option" :value="option">d{{ option }}</option>
            </select>
          </label>
        </div>
      </section>

      <section class="block">
        <div class="block-head">
          <h3>{{ text('职业', 'Classes') }}</h3>
          <span :class="['budget', { over: classBudget > 0 && classTotal !== classBudget }]">
            {{ text('等级合计', 'Levels') }} {{ classTotal }} / {{ classBudget || '—' }}
          </span>
        </div>
        <div class="rows">
          <div v-for="(level, key) in classLevels" :key="key" class="row">
            <span class="row-name">{{ key }}</span>
            <input v-model.number="classLevels[key]" type="number" min="0" max="20" />
            <button type="button" class="ghost" @click="dropClass(key)">{{ text('移除', 'Remove') }}</button>
          </div>
        </div>
        <div v-if="classNames.length" class="row add">
          <select v-model="extraClass">
            <option value="">{{ text('选择职业…', 'Pick a class…') }}</option>
            <option v-for="item in classNames" :key="item" :value="item" :disabled="Boolean(classLevels[item])">{{ item }}</option>
          </select>
          <button type="button" class="ghost" :disabled="!extraClass" @click="addClass">{{ text('添加', 'Add') }}</button>
        </div>
      </section>

      <section class="block">
        <div class="block-head">
          <h3>{{ text('技能', 'Skills') }}</h3>
          <span :class="['budget', { over: skillBudget > 0 && skillPoints > skillBudget }]">
            {{ text('点数', 'Points') }} {{ skillPoints }} / {{ skillBudget || '—' }}
          </span>
        </div>
        <div class="rows">
          <div v-for="(row, index) in skills" :key="index" class="row">
            <input v-model="row.name" type="text" list="generic-builder-skills" :placeholder="text('技能名', 'Skill')" />
            <input v-model.number="row.value" type="number" :min="1" :max="skillMax || 9" />
            <button type="button" class="ghost" @click="dropSkill(index)">{{ text('移除', 'Remove') }}</button>
          </div>
        </div>
        <datalist id="generic-builder-skills">
          <option v-for="item in availableSkills" :key="item" :value="item" />
        </datalist>
        <button
          type="button"
          class="ghost add-skill"
          :disabled="Boolean(skillCountMax) && skills.length >= skillCountMax"
          @click="addSkill"
        >{{ text('+ 加一项技能', '+ Add a skill') }}</button>
      </section>

      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>

      <footer class="foot">
        <button type="button" class="ghost" @click="emit('cancel')">{{ text('取消', 'Cancel') }}</button>
        <button type="button" class="primary" :disabled="busy || attrLeft < 0" @click="submit">
          {{ busy ? text('保存中…', 'Saving…') : text('完成并放进队伍', 'Finish and add to the party') }}
        </button>
      </footer>
    </template>

    <p v-else class="error-banner" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.generic-ruleset-builder {
  width: min(760px, 100%);
  max-height: min(86vh, 900px);
  overflow: auto;
  display: grid;
  gap: 18px;
  padding: 24px;
  border: 1px solid var(--border-color, #425064);
  border-radius: 18px;
  background: var(--card-bg, #151c27);
  color: var(--text-primary, #e7edf5);
}
.generic-ruleset-builder.embedded { width: 100%; max-height: none; border: 0; padding: 0; background: transparent; }
.head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.head h2 { margin: 4px 0 6px; font-size: 20px; }
.head p { margin: 0; font-size: 13px; color: var(--text-secondary, #9aa8ba); }
.eyebrow { font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: var(--df-interactive-strong, #64d7cf); }
.close { border: 0; background: transparent; color: inherit; font-size: 22px; line-height: 1; cursor: pointer; }
.state { padding: 24px; text-align: center; color: var(--text-secondary, #9aa8ba); }
.field { display: grid; gap: 6px; font-size: 13px; }
.field input, .attr select, .row input, .row select {
  min-height: 40px;
  padding: 0 10px;
  border: 1px solid var(--border-color, #425064);
  border-radius: 10px;
  background: var(--input-bg, #101823);
  color: inherit;
  font: inherit;
}
.block { display: grid; gap: 12px; padding-top: 6px; border-top: 1px solid var(--border-color, #2a3646); }
.block-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.block-head h3 { margin: 0; font-size: 15px; }
.budget { font-size: 12px; color: var(--text-secondary, #9aa8ba); font-variant-numeric: tabular-nums; }
.budget.over { color: var(--danger, #ff7a7a); }
.attrs { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.attr { display: grid; gap: 6px; font-size: 13px; }
.rows { display: grid; gap: 8px; }
.row { display: grid; grid-template-columns: 1fr 90px auto; gap: 8px; align-items: center; }
.row-name { font-size: 14px; }
.row.add { grid-template-columns: 1fr auto; }
.ghost {
  min-height: 40px;
  padding: 0 14px;
  border: 1px solid var(--border-color, #425064);
  border-radius: 10px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.ghost:disabled { opacity: .5; cursor: not-allowed; }
.add-skill { justify-self: start; }
.foot { display: flex; justify-content: flex-end; gap: 10px; padding-top: 6px; }
.primary {
  min-height: 42px;
  padding: 0 18px;
  border: 0;
  border-radius: 10px;
  background: var(--df-interactive-strong, #64d7cf);
  color: #06202a;
  font-weight: 600;
  cursor: pointer;
}
.primary:disabled { opacity: .6; cursor: not-allowed; }
.error-banner { margin: 0; padding: 10px 12px; border-radius: 10px; background: rgb(255 122 122 / 12%); color: var(--danger, #ff9b9b); font-size: 13px; }
</style>
