<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NInput } from 'naive-ui'
import { useLocale } from '@/composables/useLocale'
import { useSettingsStore } from '@/stores/useSettingsStore'
import {
  isReasoningEffortPreset, REASONING_EFFORT_LABEL_KEYS, reasoningEffortLevels,
  type ReasoningEffortConfigKey,
} from './reasoningEffort'

const props = defineProps<{
  configKey: ReasoningEffortConfigKey
  apiFormat?: string
  disabled?: boolean
  hint?: boolean
}>()
const store = useSettingsStore()
const { t } = useLocale()
const CUSTOM = 'custom'
const value = computed(() => String(store.config[props.configKey] || ''))
const levels = computed(() => {
  const offered = reasoningEffortLevels(props.apiFormat)
  const current = value.value
  // A stored level the provider format cannot express stays listed so it can be seen and corrected.
  return isReasoningEffortPreset(current) && !offered.includes(current) ? [current, ...offered] : offered
})

// "Custom…" is picked before an identifier exists, so the choice is tracked apart from the value.
const choosingCustom = ref(false)
const isCustom = (level: string) => !!level && !isReasoningEffortPreset(level)
const custom = computed(() => choosingCustom.value || isCustom(value.value))
const draft = ref('')
const customInput = computed(() => (isCustom(value.value) ? value.value : draft.value))

let ownWrite = false
function write(next: string) {
  if (next === value.value) return
  ownWrite = true
  store.setConfigField(props.configKey, next)
}
function change(event: Event) {
  const next = (event.target as HTMLSelectElement).value
  draft.value = ''
  if (next === CUSTOM) {
    choosingCustom.value = true
    // The identifier about to be entered replaces the previous level; empty still means default.
    write('')
  } else {
    choosingCustom.value = false
    write(next)
  }
}
function updateCustom(input: string) {
  draft.value = input
  if (input.trim()) write(input.trim())
}
// A provider change elsewhere resets the route, which also leaves custom mode.
watch(value, next => {
  if (ownWrite) ownWrite = false
  else if (!next) {
    choosingCustom.value = false
    draft.value = ''
  }
})
</script>

<template>
  <label class="reasoning-effort-field">
    <span>{{ t('reasoningEffortLabel') }}</span>
    <select :value="custom ? CUSTOM : value" :disabled="disabled" @change="change">
      <option value="">{{ t('reasoningEffortDefault') }}</option>
      <option v-for="level in levels" :key="level" :value="level">{{ t(REASONING_EFFORT_LABEL_KEYS[level]) }} ({{ level }})</option>
      <option :value="CUSTOM">{{ t('reasoningEffortCustom') }}</option>
    </select>
    <NInput v-if="custom" :value="customInput" :disabled="disabled" size="small" :placeholder="t('reasoningEffortCustomPlaceholder')" @update:value="updateCustom" />
    <small v-if="hint" class="model-role-field-hint">{{ disabled ? t('reasoningEffortChooseProvider') : t('reasoningEffortHint') }}</small>
  </label>
</template>
