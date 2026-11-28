<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NInput, NRadioButton, NRadioGroup } from 'naive-ui'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'

const PRESET_VALUES = ['off', 'low', 'medium', 'high', 'xhigh', 'ultra'] as const
type PresetValue = typeof PRESET_VALUES[number]
type EffortOption = { value: PresetValue | 'custom'; label: string }
const PRESET_LABELS: Record<PresetValue, MessageKey> = {
  off: 'reasoningEffortOff',
  low: 'reasoningEffortLow',
  medium: 'reasoningEffortMedium',
  high: 'reasoningEffortHigh',
  xhigh: 'reasoningEffortVeryHigh',
  ultra: 'reasoningEffortUltra',
}

const props = withDefaults(defineProps<{
  modelValue?: string
  disabled?: boolean
}>(), { modelValue: 'off', disabled: false })

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const { t } = useLocale()
const customValue = ref('')
const selected = ref<string>('off')

function sync(value: string) {
  const normalized = value.trim()
  if ((PRESET_VALUES as readonly string[]).includes(normalized)) {
    selected.value = normalized
    customValue.value = ''
  } else if (normalized) {
    selected.value = 'custom'
    customValue.value = normalized
  } else {
    selected.value = 'off'
    customValue.value = ''
  }
}

watch(() => props.modelValue, value => {
  const normalized = String(value || '').trim()
  if (!normalized && selected.value === 'custom') return
  sync(normalized)
}, { immediate: true })

const options = computed<EffortOption[]>(() => [
  ...PRESET_VALUES.map(value => ({
    value,
    label: t(PRESET_LABELS[value]),
  })),
  { value: 'custom', label: t('reasoningEffortCustom') },
])

function select(value: string | number) {
  const next = String(value)
  selected.value = next
  if (next !== 'custom') {
    customValue.value = ''
    emit('update:modelValue', next as PresetValue)
  } else if (customValue.value.trim()) {
    emit('update:modelValue', customValue.value.trim())
  }
}

function updateCustom(value: string) {
  customValue.value = value
  const normalized = value.trim()
  if (normalized) emit('update:modelValue', normalized)
}
</script>

<template>
  <div class="reasoning-effort-field">
    <span>{{ t('reasoningEffortLabel') }}</span>
    <NRadioGroup :value="selected" :disabled="disabled" size="small" @update:value="select">
      <NRadioButton v-for="option in options" :key="option.value" :value="option.value" :label="option.label" />
    </NRadioGroup>
    <NInput
      v-if="selected === 'custom'"
      size="small"
      :value="customValue"
      :disabled="disabled"
      :placeholder="t('reasoningEffortCustomPlaceholder')"
      @update:value="updateCustom"
    />
  </div>
</template>
