<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CharacterSkill, RuleMeta, SkillSpec } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { localizedField, skillPointCost } from '@/utils/ruleSchema'

const SKILL_EFFECT_MAX_CHARS = 500
const props = defineProps<{ modelValue: CharacterSkill[]; pool?: Array<string | SkillSpec>; meta?: RuleMeta | null }>()
const emit = defineEmits<{ 'update:modelValue': [v: CharacterSkill[]] }>()
const { t } = useLocale()

const skills = computed<CharacterSkill[]>({
  get: () => props.modelValue || [],
  set: (v) => emit('update:modelValue', v),
})
const pool = computed(() => props.pool || [])
function poolName(s: string | SkillSpec) { return typeof s === 'string' ? s : s.name || s.key || '' }

function add(name?: string) {
  skills.value = [...skills.value, { name: name || '', value: 20 }]
}
function remove(i: number) {
  skills.value = skills.value.filter((_, idx) => idx !== i)
}
function updateName(i: number, v: string) {
  const arr = [...skills.value]
  arr[i] = { ...arr[i], name: v }
  skills.value = arr
}
function updateVal(i: number, v: number) {
  const arr = [...skills.value]
  arr[i] = { ...arr[i], value: v || 0 }
  skills.value = arr
}
function updateEffect(i: number, v: string) {
  const arr = [...skills.value]
  const effect = v.slice(0, SKILL_EFFECT_MAX_CHARS)
  if (effect) {
    arr[i] = { ...arr[i], effect }
  } else {
    // 清空时移除字段，避免旧卡/空说明被写成空字符串。
    const { effect: _cleared, ...rest } = arr[i]
    arr[i] = rest as CharacterSkill
  }
  skills.value = arr
}
// 效果说明默认折叠：技能多时不应铺开一排 textarea。
const openEffects = ref<Set<number>>(new Set())
function toggleEffect(i: number) {
  const next = new Set(openEffects.value)
  if (next.has(i)) next.delete(i)
  else next.add(i)
  openEffects.value = next
}

const skillHint = computed(() => localizedField<string>(props.meta, 'skill_hint') || '')
const maxSkills = computed(() => Number(props.meta?.max_skills || 0))
const skillPointTotal = computed(() => Number(props.meta?.skill_point_total || 0))
const maxSkillValue = computed(() => Number(props.meta?.max_skill_value || 0))
const filledSkills = computed(() => skills.value.filter(s => s.name.trim()))
const skillSpent = computed(() => filledSkills.value.reduce((sum, skill) => sum + skillPointCost(skill, props.meta), 0))
const skillOverLimit = computed(() =>
  Boolean((maxSkills.value && filledSkills.value.length > maxSkills.value)
    || (skillPointTotal.value && skillSpent.value > skillPointTotal.value)
    || (maxSkillValue.value && skills.value.some(s => (Number(s.value || 0) || 0) > maxSkillValue.value)))
)
</script>

<template>
  <div class="skill-editor">
    <p v-if="skillHint" class="muted sheet-hint">{{ skillHint }}</p>
    <p class="muted sheet-hint" :class="{ warn: skillOverLimit }">
      <span v-if="maxSkills">{{ t('skillCount', { count: filledSkills.length, max: maxSkills }) }}</span>
      <span v-if="skillPointTotal"> · {{ t('skillPointsSpent', { spent: skillSpent, total: skillPointTotal }) }}</span>
      <span v-if="maxSkillValue"> · {{ t('maxSingleSkill', { max: maxSkillValue }) }}</span>
    </p>
    <div v-for="(s, i) in skills" :key="i" class="skill-block">
      <div class="skill-row">
        <input :value="s.name" :placeholder="t('skillName')" @input="updateName(i, ($event.target as HTMLInputElement).value)">
        <input type="number" :value="s.value" min="0" :class="{ warn: maxSkillValue && (Number(s.value || 0) || 0) > maxSkillValue }" @input="updateVal(i, Number(($event.target as HTMLInputElement).value))">
        <button
          type="button"
          class="chip skill-effect-toggle"
          :class="{ active: openEffects.has(i) || Boolean(s.effect) }"
          :aria-expanded="openEffects.has(i)"
          :title="t('skillEffect')"
          @click="toggleEffect(i)"
        >{{ t('skillEffect') }}</button>
        <button class="modal-x" :title="t('delete')" @click="remove(i)">×</button>
      </div>
      <textarea
        v-if="openEffects.has(i)"
        class="skill-effect-input"
        rows="2"
        :maxlength="SKILL_EFFECT_MAX_CHARS"
        :value="s.effect || ''"
        :placeholder="t('skillEffectPlaceholder')"
        @input="updateEffect(i, ($event.target as HTMLTextAreaElement).value)"
      />
    </div>
    <button class="chip" @click="add()">+ {{ t('addSkill') }}</button>
    <div v-if="pool.length" class="skill-pool">
      <button v-for="s in pool" :key="poolName(s)" class="chip" @click="add(poolName(s))">{{ poolName(s) }}</button>
    </div>
  </div>
</template>
