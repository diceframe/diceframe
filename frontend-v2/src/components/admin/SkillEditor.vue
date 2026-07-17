<script setup lang="ts">
import { computed } from 'vue'
import type { CharacterSkill, SkillSpec } from '@/api/types'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{ modelValue: CharacterSkill[]; pool?: Array<string | SkillSpec> }>()
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
</script>

<template>
  <div class="skill-editor">
    <div v-for="(s, i) in skills" :key="i" class="skill-row">
      <input :value="s.name" :placeholder="t('skillName')" @input="updateName(i, ($event.target as HTMLInputElement).value)">
      <input type="number" :value="s.value" min="0" @input="updateVal(i, Number(($event.target as HTMLInputElement).value))">
      <button class="modal-x" :title="t('delete')" @click="remove(i)">×</button>
    </div>
    <button class="chip" @click="add()">+ {{ t('addSkill') }}</button>
    <div v-if="pool.length" class="skill-pool">
      <button v-for="s in pool" :key="poolName(s)" class="chip" @click="add(poolName(s))">{{ poolName(s) }}</button>
    </div>
  </div>
</template>
