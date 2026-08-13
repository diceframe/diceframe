<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MapBackgroundOption } from '@/api/types'
import {
  BUILTIN_MAP_BACKGROUNDS,
  MAP_BACKGROUND_ACCEPT,
  validateMapBackgroundFile,
} from '@/api/mapBackgrounds'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'

const props = withDefaults(defineProps<{
  modelValue: string
  file?: File | null
  options?: MapBackgroundOption[]
}>(), {
  file: null,
  options: () => [],
})
const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:file': [value: File | null]
}>()
const { t } = useLocale()
const toast = useToast()
const input = ref<HTMLInputElement | null>(null)

const pluginOptions = computed(() => props.options.filter(option => option.kind === 'plugin' && option.selection?.map_id))
const uploadOptions = computed(() => props.options.filter(option => option.kind === 'upload' && option.selection?.asset_id))

function onSelection(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  emit('update:modelValue', value)
  if (value !== 'file') emit('update:file', null)
}

function onFile(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0] || null
  target.value = ''
  if (!file) return
  try {
    validateMapBackgroundFile(file)
  } catch {
    toast.error(t('mapBackgroundInvalidFile'))
    return
  }
  emit('update:file', file)
  emit('update:modelValue', 'file')
}
</script>

<template>
  <section class="map-background-picker">
    <label>
      <span>{{ t('mapBackgroundTitle') }}</span>
      <select :value="modelValue" @change="onSelection">
        <option value="auto">{{ t('mapBackgroundAuto') }}</option>
        <option value="none">{{ t('mapBackgroundNone') }}</option>
        <option v-for="item in BUILTIN_MAP_BACKGROUNDS" :key="item.id" :value="`builtin:${item.id}`">
          {{ t(`mapBackground_${item.id}`) }}
        </option>
        <option
          v-for="option in uploadOptions"
          :key="option.id"
          :value="`upload:${option.selection?.asset_id}`"
        >{{ t('mapBackgroundCurrentUpload') }}</option>
        <option v-if="file" value="file">{{ t('mapBackgroundNewUpload', { name: file.name }) }}</option>
        <optgroup v-if="pluginOptions.length" :label="t('mapBackgroundPlugins')">
          <option
            v-for="option in pluginOptions"
            :key="option.id"
            :value="`plugin-map:${option.selection?.map_id}`"
          >{{ option.name }}{{ option.plugin_name ? ` · ${option.plugin_name}` : '' }}</option>
        </optgroup>
      </select>
      <small>{{ t('mapBackgroundHint') }}</small>
    </label>
    <button type="button" @click="input?.click()">{{ t('mapBackgroundUpload') }}</button>
    <input ref="input" type="file" :accept="MAP_BACKGROUND_ACCEPT" hidden @change="onFile">
  </section>
</template>
